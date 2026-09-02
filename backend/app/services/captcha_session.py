"""BMS 验证码交互登录会话管理器

闪猫等数据源登录需要验证码,现采用「自动识别优先 + 人工兜底」双轨流程:
  自动识别(ddddocr):
    1. 后端进入登录页,截取验证码图片 → ddddocr 识别 → 自动填账号密码+验证码提交登录
    2. 识别成功且登录通过 → 保存 storage_state,直接返回 success,前端无需用户操作
  人工兜底(识别失败 / 验证码错误时):
    1. 前端 POST /fetch/captcha-login/start → 后端启动 Playwright,进入登录页,截取验证码图片,返回 session_id + base64
    2. 用户在前端页面输入验证码 → POST /fetch/captcha-login/submit {session_id, captcha}
    3. 后端填账号密码+验证码提交登录,成功后保存 storage_state 文件供后续抓取复用

会话最多存活 10 分钟(用户不提交就自动清理)。
"""

import asyncio
import base64
import logging
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 会话最大存活秒数(用户读验证码+输入的允许时间)
SESSION_TTL = 600  # 10 分钟

# ddddocr 全局单例(首次调用懒加载,避免每次启动都加载 ONNX 模型)
# 两个单例:
#   - beta=False(默认) 纯英数验证码模型,识别率 ~95%
#   - beta=True(Beta 中文混合模型) 处理含中文汉字/中文数字的验证码(闪猫就是这种)
# 调用端会优先用 beta=True 识别,失败再 fallback 到 beta=False。
_ocr_instance_beta_false = None
_ocr_instance_beta_true = None
_ocr_init_lock = False  # 简单线程/协程安全标记(加载时串行)


def get_ocr(beta: bool = False) -> Optional[object]:
    """懒加载 ddddocr 实例(支持 beta=True 中文混合模型)。

    加载失败返回 None,此时自动走人工兜底。
    ddddocr 首次 import 会加载 ~20MB ONNX 模型到内存,约 100-300ms,
    用全局单例避免每次识别都重建模型。
    """
    global _ocr_instance_beta_false, _ocr_instance_beta_true, _ocr_init_lock
    target = _ocr_instance_beta_true if beta else _ocr_instance_beta_false
    if target is not None:
        return target
    if _ocr_init_lock:
        # 其他协程正在初始化,直接返回 None 让这一次走人工兜底/其他模型
        return None
    _ocr_init_lock = True
    try:
        import ddddocr  # type: ignore
        # show_ad=False 关闭控制台广告
        inst = ddddocr.DdddOcr(show_ad=False, beta=beta)
        logger.info(
            "[CaptchaOCR] ddddocr 模型加载成功(beta=%s),验证码自动识别已启用",
            beta,
        )
        if beta:
            _ocr_instance_beta_true = inst
        else:
            _ocr_instance_beta_false = inst
        return inst
    except Exception as e:
        logger.warning(
            "[CaptchaOCR] ddddocr 加载失败(beta=%s),自动识别功能该模型禁用。原因: %s",
            beta, e,
        )
        # 注意: 必须置 None,不能用 False(否则下次 target is not None 命中返回 bool,会 crash 调用 classification)
        if beta:
            _ocr_instance_beta_true = None
        else:
            _ocr_instance_beta_false = None
        return None
    finally:
        _ocr_init_lock = False


# 合法验证码校验: 常见组合为「中文汉字 + 大写/小写字母 + 数字」混合,
# 某些系统(SCRM 白音)是「英数 + 数学运算符号」但本质不是数学计算而是直接输入原文。
# 不区分字符集(ddddocr 可能识别出中文数字/中文),长度通常 3-8 位,放宽到 3-12 位兜底。
# 常见字符集:
#   - 英数: [A-Za-z0-9]
#   - 中文: 常用汉字区 \u4e00-\u9fff(含中文数字零一二三四五六七八九十百千等)
#   - 允许常见符号: +-*/=. 以及大小写 x/X(常被写成乘号)
_CAPTCHA_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9+\-*/=.xX]{3,12}$")


# ──────────────────────────────────────────────────────────────────────
# 置信度预检(OCR 识别出的结果每个字符带概率,低置信度直接刷新验证码重来,不登录)
# ──────────────────────────────────────────────────────────────────────
# 单字符最低置信度阈值(0-1):低于这个值 → 视为识别不确定 → 重新刷新验证码再认
_OCR_MIN_CHAR_PROB: float = 0.72
# 整串平均置信度阈值:必须同时满足 min_char 和 avg 两个都达标才算"有把握"
_OCR_AVG_PROB: float = 0.86
# OCR 预检最多刷新验证码次数(不登录,只刷新+识别);超过次数就拿"置信度相对最好的那一次"去登录兜底
_OCR_PRECHECK_MAX_REFRESH: int = 20
# 退化置信度:ddddocr 版本不支持 probability 参数或抛异常时,用这个中等置信度兜底(让它走一次登录)
_OCR_FALLBACK_CONFIDENCE: float = 0.70


def _coerce_prob_list(raw) -> tuple[str | None, list[float]]:
    """把 ddddocr.classification(probability=True) 返回的多种可能格式,统一成 (code, probs_list)。

    兼容以下几种常见 ddddocr 版本输出:
      1) list[list/tuple]: [ ['Q', 0.92], ['七', 0.88], ... ]  —— 老版本直接返回「字符+概率」
      2) dict(text=..., probabilities=ND-Matrix): 新版本返回 { "text": "QKVXB",
           "probabilities": (1,N_CLASSES) / (N_CHAR,N_CLASSES) / (1,N_CHAR,N_CLASSES) }
         —— 需要对「每个字符位置的概率向量取 max」,作为该字符的预测置信度
      3) 直接返回 str: 说明这个安装版本不支持 probability 参数(退化情况,外层会 fallback 到普通 classification)
    不兼容情况 → 返回 (None, [])
    """
    if raw is None:
        return None, []
    if isinstance(raw, str):
        return None, []
    chars: list[str] = []
    probs: list[float] = []

    # ── 分支 A: 新版本 dict(text+probabilities+charset+confidence, ddddocr>=1.4+ 常见输出) ──
    if isinstance(raw, dict):
        text = raw.get("text")
        probs_raw = raw.get("probabilities")
        charset = raw.get("charset")
        if isinstance(text, str) and text and probs_raw is not None:
            # 把 numpy 数组 → list
            try:
                import numpy as _np  # type: ignore
                if isinstance(probs_raw, _np.ndarray):
                    probs_raw = probs_raw.tolist()
                if isinstance(charset, _np.ndarray):
                    charset = charset.tolist()
            except Exception:
                pass

            # ddddocr 的 probabilities 真实结构(30 个 CTC 时间步 × batch=1 × 字符集 8210 维):
            #   probabilities = [ step0, step1, ..., step29 ]
            #   其中每个 step = [ [ 0号类别概率, 1号类别概率, ..., 8209号类别概率 ] ]  # 最内层 1 是 batch
            # charset[0] 永远是空字符串 '' (CTC blank),非 0 才是真实输出字符。
            # 非空字符置信度提取算法:
            #   1) 遍历 30 个时间步,剥掉每个 step 的 batch=1 维 → vec(8210,)
            #   2) argmax(vec) = ci, if ci==0 → blank 跳过;否则记录 (charset[ci], max(vec))
            #   3) 拼出来的字符串必须等于 text, 否则视为解析失败走退化
            per_char_probs: list[float] = []
            built_chars: list[str] = []
            steps = probs_raw if isinstance(probs_raw, (list, tuple)) else []
            ok_shape = True
            for step in steps:
                vec = step
                # 剥掉 batch=1 外层:每个 step 最常见 shape 是 [1, 8210]
                for _ in range(2):
                    if isinstance(vec, (list, tuple)) and len(vec) == 1:
                        vec = vec[0]
                    else:
                        break
                if not isinstance(vec, (list, tuple)) or len(vec) < 2:
                    ok_shape = False
                    break
                try:
                    nums = [float(x) for x in vec if isinstance(x, (int, float))]
                except Exception:
                    ok_shape = False
                    break
                if not nums:
                    ok_shape = False
                    break
                # argmax + max
                mx = -1.0
                amx = 0
                for idx, v in enumerate(nums):
                    if v > mx:
                        mx = v
                        amx = idx
                # 空字符跳过
                if amx == 0:
                    continue
                mx = max(0.0, min(1.0, mx))
                # 查对应字符
                ch = ""
                if isinstance(charset, (list, tuple, str)) and 0 <= amx < len(charset):
                    raw_ch = charset[amx]
                    if isinstance(raw_ch, str):
                        ch = raw_ch
                    elif isinstance(raw_ch, (int, float)):
                        ch = str(raw_ch)
                if not ch:
                    # 字符集里没拿到对应字符,但这是真实非空步,先用空占位,后续长度校验会不通过
                    built_chars.append("")
                    per_char_probs.append(mx)
                    continue
                built_chars.append(ch)
                per_char_probs.append(mx)
            if ok_shape and len(built_chars) == len(text) and "".join(built_chars) == text:
                return text, per_char_probs
            # 不匹配也仍然尝试:长度对得上就行,忽略字符(有些版本 charset 不一致)
            if ok_shape and len(per_char_probs) == len(text) and len(per_char_probs) > 0:
                # 长度相等,直接返回概率列表(哪怕字符不完全匹配,也比退化值 0.7 更准)
                return text, per_char_probs
            # 否则返回 None 让外层 fallback 到纯字符串 + 退化置信度
            return None, []
        # dict 里没有 text+probabilities → 按 items 作为 (char,prob) 兜底
        items = raw.items()
    else:
        # list/tuple 作为 items 列表
        items = raw  # type: ignore[assignment]

    # ── 分支 B: 老版本 list[list/tuple(char, prob)] ──
    try:
        for item in items:
            c: str | None = None
            p: float | None = None
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x0, x1 = item[0], item[1]  # type: ignore[misc]
                if isinstance(x0, str):
                    c = x0
                if isinstance(x1, (int, float)):
                    p = float(x1)
                elif isinstance(x0, (int, float)) and isinstance(x1, str):
                    # 反了: (prob, char)
                    p = float(x0)
                    c = x1
            elif isinstance(item, dict):
                for key_char in ("char", "c", "ch", "word", "character", "label"):
                    if isinstance(item.get(key_char), str):
                        c = item[key_char]
                        break
                for key_prob in ("prob", "p", "probability", "score", "confidence"):
                    if isinstance(item.get(key_prob), (int, float)):
                        p = float(item[key_prob])
                        break
            if c is None or p is None:
                continue
            if not isinstance(c, str) or len(c) == 0:
                continue
            p = max(0.0, min(1.0, float(p)))
            chars.append(c)
            probs.append(p)
    except Exception:
        return None, []
    if not chars or not probs or len(chars) != len(probs):
        return None, []
    return "".join(chars), probs


def try_ocr_captcha_with_confidence(png_bytes: bytes) -> Optional[dict]:
    """带置信度的 OCR 识别(用于预检:不登录,只看识别结果有没有把握)。

    Returns:
      dict 结构:
        {
          "code": str              # 识别出的验证码字符串(已通过 _CAPTCHA_RE + 转大写)
          "used_beta": bool        # 用的是 beta=True(中文混合) 还是 False(纯英数)
          "min_prob": float        # 所有字符中最低的那个概率
          "avg_prob": float        # 平均概率
          "probs": list[float]     # 每个字符的概率(长度 = len(code))
          "chars": list[str]       # 每个字符(便于调试展示)
          "confident": bool        # 综合评分:min>=_OCR_MIN_CHAR_PROB and avg>=_OCR_AVG_PROB
        }
      识别失败 → 返回 None。
    """
    if not png_bytes:
        return None
    # 先按两个模型依次尝试(和 try_ocr_captcha 顺序一致)
    for beta in (True, False):
        ocr = get_ocr(beta=beta)
        if ocr is None:
            continue
        code: str | None = None
        probs: list[float] = []
        # 先尝试 probability=True 模式(可能抛异常)
        try:
            raw = ocr.classification(png_bytes, probability=True)
            coerced_code, coerced_probs = _coerce_prob_list(raw)
            if coerced_code and coerced_probs:
                code = coerced_code
                probs = coerced_probs
        except (TypeError, ValueError, Exception):
            # 老版本 ddddocr 不接受 probability 参数 → 退化:跑一次普通 classification,置信度按中等估
            pass
        if code is None:
            # 退化:普通 classification 拿 code,概率填退化值中等置信度(每个字符一样)
            try:
                plain = ocr.classification(png_bytes)
            except Exception:
                continue
            if not isinstance(plain, str):
                continue
            tmp = plain.strip()
            if not _CAPTCHA_RE.match(tmp):
                continue
            code = tmp
            probs = [_OCR_FALLBACK_CONFIDENCE] * len(tmp)
        else:
            # probability=True 模式拿到了 code+probs,但仍然要过格式校验
            if not _CAPTCHA_RE.match(code):
                continue
        # 走到这里 code 和 probs 都有了
        if len(code) != len(probs):
            # 理论上不会,兜底一下
            if len(probs) == 0:
                probs = [_OCR_FALLBACK_CONFIDENCE] * len(code)
            else:
                # 截断到最短
                m = min(len(code), len(probs))
                code = code[:m]
                probs = probs[:m]
        # 统一转大写(中文保持不变,对 upper 幂等)
        code = code.upper()
        min_p = min(probs) if probs else 0.0
        avg_p = (sum(probs) / len(probs)) if probs else 0.0
        confident = (min_p >= _OCR_MIN_CHAR_PROB) and (avg_p >= _OCR_AVG_PROB)
        return {
            "code": code,
            "used_beta": beta,
            "min_prob": round(min_p, 4),
            "avg_prob": round(avg_p, 4),
            "probs": [round(x, 4) for x in probs],
            "chars": list(code),
            "confident": confident,
        }
    # 两个模型都没识别出来
    return None


def try_ocr_captcha(png_bytes: bytes) -> Optional[str]:
    """尝试用 ddddocr 识别验证码图片(双模型 fallback: 先中文混合,再纯英数)。

    注意:这是「不带置信度」的兼容版本,仅返回识别字符串。
    新代码优先使用 try_ocr_captcha_with_confidence,能利用置信度预检避免登录时反复触发"验证码错误"。
    """
    r = try_ocr_captcha_with_confidence(png_bytes)
    if r is None:
        # 兼容旧逻辑:识别格式异常时,仍然尝试把图片存到 /tmp 便于复盘
        if png_bytes:
            try:
                import tempfile
                import uuid
                fn = Path(tempfile.gettempdir()) / f"captcha_failed_{uuid.uuid4().hex[:8]}.png"
                fn.write_bytes(png_bytes)
                logger.info(f"[CaptchaOCR] 两个模型均未识别出合法验证码,图片已存: {fn}")
            except Exception:
                pass
        return None
    return r["code"]


@dataclass
class CaptchaSession:
    session_id: str
    data_type: str
    # Playwright 资源
    playwright: object = None  # async_playwright() 返回值
    browser: object = None
    context: object = None
    page: object = None
    # 登录配置(运行时填充)
    base_url: str = ""
    page_path: str = ""
    username: str = ""
    password: str = ""
    created_at: float = field(default_factory=time.time)
    state_path: Path = None  # storage_state 保存路径

    async def cleanup(self):
        if self.page:
            with suppress(Exception):
                await self.page.close()
        if self.context:
            with suppress(Exception):
                await self.context.close()
        if self.browser:
            with suppress(Exception):
                await self.browser.close()
        if self.playwright:
            with suppress(Exception):
                await self.playwright.stop()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None


class CaptchaSessionManager:
    """简单内存会话管理: session_id → CaptchaSession"""

    def __init__(self):
        self._sessions: dict[str, CaptchaSession] = {}

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _is_expired(self, s: CaptchaSession) -> bool:
        return (time.time() - s.created_at) > SESSION_TTL

    async def _purge_expired(self):
        expired = [sid for sid, s in self._sessions.items() if self._is_expired(s)]
        for sid in expired:
            logger.info(f"[CaptchaSession] 会话过期自动清理: {sid}")
            s = self._sessions.pop(sid, None)
            if s:
                await s.cleanup()

    async def create(self, data_type: str, base_url: str, page_path: str,
                     username: str, password: str, state_path: Path) -> CaptchaSession:
        await self._purge_expired()
        sid = self._new_id()
        sess = CaptchaSession(
            session_id=sid,
            data_type=data_type,
            base_url=base_url,
            page_path=page_path,
            username=username,
            password=password,
            state_path=state_path,
        )
        self._sessions[sid] = sess
        logger.info(f"[CaptchaSession] 创建会话 {sid} 用于 {data_type}")
        return sess

    def get(self, session_id: str) -> Optional[CaptchaSession]:
        s = self._sessions.get(session_id)
        if s and self._is_expired(s):
            asyncio.create_task(self.remove(session_id))
            return None
        return s

    async def remove(self, session_id: str):
        s = self._sessions.pop(session_id, None)
        if s:
            await s.cleanup()
            logger.info(f"[CaptchaSession] 会话已销毁: {session_id}")


# 全局单例
captcha_session_manager = CaptchaSessionManager()


def bytes_to_data_uri(png_bytes: bytes) -> str:
    """把 PNG 字节转成 data: URI(前端 <img src=...> 直接可用)"""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ──────────────────────────────────────────────────────────────────────
# 数学计算式验证码识别 & 安全计算 (SCRM 新版企微 专用)
#
# 验证码形式: "7*0=?" / "9-9=?" / "5+3≈?" / "12÷4=?" / "(1+2)*3=?"
# 处理流程:
#   1. OCR 识别出算式字符串 (复用 try_ocr_captcha_with_confidence)
#   2. 符号归一化 (≈→=, ×/x/X/*→*, ÷→/, −/—→-)
#   3. 正则剥离尾部 =? 或 ≈? 拿到纯表达式
#   4. ast.parse 白名单方式安全计算 (仅允许 数字/加减乘除/括号)
#   5. 返回计算结果字符串 (整数取整去尾,浮点数保留最多2位小数)
# ──────────────────────────────────────────────────────────────────────

import ast as _ast
import re as _re

# 符号归一化映射表 (OCR 可能把 × 识别成 x/X, 把 ÷ 识别成 其他,把 ≈ 和 = 混用)
# 新增: OCR 常见把 + 号识别为 V/T/Y/U (形状相近), 故也做映射.
#       注意: X/x 还是保留为乘号 (*), 不要覆盖成加号.
_MATH_SYMBOL_NORMALIZE_MAP = {
    "×": "*", "x": "*", "X": "*", "✕": "*", "✖": "*", "❌": "*",
    "÷": "/", "／": "/", "∕": "/",
    "−": "-", "—": "-", "–": "-", "―": "-", "‑": "-",
    "＋": "+", "➕": "+",
    # --- OCR 常见误识别: 加号 (+) 被识别成 V/Y/U (形状相近) ---
    "V": "+", "v": "+",
    "Y": "+", "y": "+",
    "U": "+", "u": "+",
    # --- OCR 常见误识别: 乘号 (×) 被识别成 T/t (更像 T 的形状: 上面一横下面一竖) ---
    "T": "*", "t": "*",
    # --- OCR 常见误识别: 等号(=) 被识别成 E/e/≈ 等 ---
    "E": "=", "e": "=",
    "（": "(", "）": ")", "〔": "(", "〕": ")",
    "≈": "=", "≡": "=", "＝": "=", "﹦": "=",
    "？": "?", "❓": "?",
    ",": "", "，": "",  # 千分位分隔符直接去掉
    " ": "", "\t": "", "\u3000": "",  # 所有空白去掉
}

# 合法数学表达式字符集 (归一化后): 数字 点 加减乘除 括号
_MATH_EXPR_VALID_RE = _re.compile(r"^[0-9.+\-*/()]+$")
# 尾部 =? 剥离 (支持 "expr=?" / "expr ≈ ?" / "expr＝？" 等多种写法,归一化后都是 expr=?)
_MATH_TAIL_STRIP_RE = _re.compile(r"=\??$")
# 头部非表达式字符清理 (OCR 可能识别出前缀如 "验证码:7*0=?")
_MATH_HEAD_CLEAN_RE = _re.compile(r"^[^0-9(]+")


def _normalize_math_captcha_text(raw_text: str) -> str:
    """归一化 OCR 出来的算式文本:符号映射 + 空白去除"""
    if not raw_text:
        return ""
    tmp = raw_text
    for k, v in _MATH_SYMBOL_NORMALIZE_MAP.items():
        tmp = tmp.replace(k, v)
    # 再走一次空白字符彻底清除(上面 map 里已经清过,保险起见)
    tmp = "".join(tmp.split())
    return tmp


def _safe_eval_math_expr(expr: str) -> float | int | None:
    """使用 ast.NodeTransformer 白名单方式安全计算数学表达式(永不调用 eval)。

    仅允许:
      - Constant(int/float) 数字
      - BinOp: Add/Sub/Mult/Div
      - UnaryOp: UAdd/USub (正负数,如 -3, +5)
    不允许: 任何变量/函数/比较/布尔/位运算/幂运算 等.
    """
    if not expr or not _MATH_EXPR_VALID_RE.match(expr):
        return None
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    # 用 ast.NodeVisitor 白名单检查 + 计算
    def _calc(node):
        if isinstance(node, _ast.Expression):
            return _calc(node.body)
        if isinstance(node, _ast.Constant):
            v = node.value
            if isinstance(v, bool):  # bool 是 int 子类,但不允许
                return None
            if isinstance(v, (int, float)):
                return v
            return None
        # Python 3.7-3.8 兼容: ast.Num (3.8 之前的旧写法)
        if hasattr(_ast, "Num") and isinstance(node, getattr(_ast, "Num")):
            v = node.n  # type: ignore[attr-defined]
            if isinstance(v, (int, float)):
                return v
            return None
        if isinstance(node, _ast.UnaryOp):
            operand = _calc(node.operand)
            if operand is None:
                return None
            if isinstance(node.op, _ast.UAdd):
                return +operand
            if isinstance(node.op, _ast.USub):
                return -operand
            return None
        if isinstance(node, _ast.BinOp):
            left = _calc(node.left)
            right = _calc(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, _ast.Add):
                return left + right
            if isinstance(node.op, _ast.Sub):
                return left - right
            if isinstance(node.op, _ast.Mult):
                return left * right
            if isinstance(node.op, _ast.Div):
                if right == 0:  # 除零保护 (OCR 识别错可能导致)
                    return None
                return left / right
            return None
        return None  # 其他任何节点类型都不允许

    result = _calc(tree)
    if result is None:
        return None
    if isinstance(result, float) and not (float("-inf") < result < float("inf")):
        return None  # 排除 NaN / inf
    # 整数判定:浮点数尾部为 0 → 转回 int (避免显示 7.0)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def _extract_math_expr_from_normalized(normalized: str) -> str | None:
    """从归一化后的文本中剥离 =? 尾部,拿到纯表达式部分"""
    if not normalized:
        return None
    # 1. 清头部非表达式垃圾字符 (OCR 可能把图形也识别出前缀字符)
    cleaned = _MATH_HEAD_CLEAN_RE.sub("", normalized)
    # 2. 剥离尾部 =? = ?? ? =? 等
    cleaned = _MATH_TAIL_STRIP_RE.sub("", cleaned)
    # 3. 再走一次:如果尾部还有单独的 = 或 ? 残留,继续剥
    while cleaned and cleaned[-1] in ("=", "?"):
        cleaned = cleaned[:-1]
    if not cleaned:
        return None
    # 4. 合法性再检查一遍
    if not _MATH_EXPR_VALID_RE.match(cleaned):
        return None
    # 5. 括号必须配对 (快速检查)
    depth = 0
    for ch in cleaned:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
    if depth != 0:
        return None
    return cleaned


# ── 强力 fallback: OCR 经常把符号识别成字母/数字,尝试多种假设组合算出合法答案 ──
# 不同假设的符号映射变体(用于 OCR 把不同符号识别成同一个字母时的多候选尝试)
_MATH_SYMBOL_FALLBACK_VARIANTS: list[dict[str, str]] = [
    # H0: 默认假设(T→* ; V/Y/U→+ ; X→* ; E→=  ) — 已在 _MATH_SYMBOL_NORMALIZE_MAP 中实现
    {},
    # H1: T 是加号 + (有些字体 T 和 + 相似)
    {"T": "+", "t": "+"},
    # H2: V 是除号 ÷ (少见但尝试)
    {"V": "/", "v": "/"},
    # H3: Y/U/V 可能都是乘号 ×
    {"Y": "*", "y": "*", "U": "*", "u": "*", "V": "*", "v": "*"},
    # H4: 常见「数字被识别为字母」修正(S→5, G→6, B→8, l/I→1, O/o/Q→0, Z→2, q→9)
    {"S": "5", "s": "5", "G": "6", "g": "6", "B": "8",
     "I": "1", "l": "1", "O": "0", "o": "0", "Q": "0",
     "Z": "2", "z": "2"},
]


def _apply_symbol_map(raw_text: str, extra_overrides: dict[str, str]) -> str:
    """应用符号映射 + 额外覆盖表,返回归一化后字符串。

    顺序:先应用 override(针对 T→* 这类特殊假设,保证 override 不会被默认映射覆盖),
    再应用默认 _MATH_SYMBOL_NORMALIZE_MAP(除 override 已处理的 key)。
    """
    tmp = raw_text
    used_keys: set[str] = set()
    # Step 1: 先应用 override (保证特殊假设生效, T→* 就真的变成 *,不会先被默认映射成 +)
    for k, v in extra_overrides.items():
        if k in tmp:
            tmp = tmp.replace(k, v)
            used_keys.add(k)
    # Step 2: 默认映射,但跳过 override 用过的 key(避免覆盖用户假设)
    for k, v in _MATH_SYMBOL_NORMALIZE_MAP.items():
        if k in used_keys:
            continue
        if k in tmp:
            tmp = tmp.replace(k, v)
    return "".join(tmp.split())


def _score_math_candidate(expr: str, val: float | int,
                          tail_strip: int = 0,
                          variant_idx: int = 0) -> int:
    """给数学验证码候选项打分,分数越高越合理(越大越优先)。

    规则:
      - 整数 > 近似整数 > 非整数浮点数 (+5000 / +2500 / -500)
      - 答案在 0~99 合理范围 +500,100~199 +200,超过 1000 或负数 -800
      - 操作数位差(两个数字位数)大的扣分(避免 4+57 / 9+39 这种垃圾题)
      - 尾部剥越少越优先(-tail_strip*10); variant_idx 越小越优先
    """
    score = 1000 - tail_strip * 10 - variant_idx * 5
    if isinstance(val, int):
        score += 5000
    elif float(val).is_integer():
        score += 2500
    else:
        score -= 500
    if isinstance(val, int):
        if 0 <= val <= 99:
            score += 500
        elif 100 <= val <= 199:
            score += 200
        elif val > 1000 or val < 0:
            score -= 800
    # 操作数位差惩罚
    try:
        parts = _re.split(r"[+\-*/]", expr)
        if len(parts) >= 2:
            lens = []
            for p in parts:
                dig = "".join(ch for ch in p if ch.isdigit())
                if dig:
                    lens.append(len(dig))
            if len(lens) >= 2:
                diff = abs(lens[0] - lens[1])
                if diff >= 1:
                    score -= (200 + 300 * diff)  # 差 1 位扣 500,差 2 位扣 800
    except Exception:
        pass
    return score


def _aggressive_try_math_compute(raw_text: str) -> Optional[dict]:
    """对 OCR 原始字符串穷举多种「符号映射假设 × 尾部截断长度」,返回第一个算出合法整数的结果。

    Returns:
        dict = {"expr": str, "answer": int|float, "tail_strip": int, "variant_idx": int}
        任何组合都算不出 → 返回 None
    """
    if not raw_text:
        return None
    base_text = raw_text.strip()
    if not base_text:
        return None

    best: Optional[dict] = None
    best_score = -1_000_000

    for vi, overrides in enumerate(_MATH_SYMBOL_FALLBACK_VARIANTS):
        mapped = _apply_symbol_map(base_text, overrides)
        if not mapped:
            continue
        max_strip = min(5, max(0, len(mapped) - 3))
        for tail_strip in range(0, max_strip + 1):
            cur = mapped[:-tail_strip] if tail_strip > 0 else mapped
            if len(cur) < 3:
                continue
            cleaned = _MATH_HEAD_CLEAN_RE.sub("", cur)
            cleaned = _MATH_TAIL_STRIP_RE.sub("", cleaned)
            while cleaned and cleaned[-1] in ("=", "?"):
                cleaned = cleaned[:-1]
            # 表达式必须数字/右括号结尾 (否则一直剥到是为止)
            while cleaned and cleaned[-1] not in "0123456789)":
                cleaned = cleaned[:-1]
            if len(cleaned) < 3:
                continue
            if not _MATH_EXPR_VALID_RE.match(cleaned):
                continue
            # 至少要有一个运算符 (纯数字就不算计算式了)
            if not any(op in cleaned for op in ("+", "-", "*", "/")):
                continue
            # 括号配对
            depth = 0
            bad_paren = False
            for ch in cleaned:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        bad_paren = True
                        break
            if bad_paren or depth != 0:
                continue
            val = _safe_eval_math_expr(cleaned)
            if val is None:
                continue
            if not (-99999 <= val <= 99999):
                continue
            score = _score_math_candidate(cleaned, val, tail_strip=tail_strip, variant_idx=vi)
            if score > best_score:
                best_score = score
                best = {
                    "expr": cleaned,
                    "answer": val,
                    "tail_strip": tail_strip,
                    "variant_idx": vi,
                }
    return best


def _format_math_answer(val: float | int) -> str:
    """格式化计算结果为字符串:整数取整,浮点数最多2位小数(去尾零)"""
    if isinstance(val, int):
        return str(val)
    # float 情况
    rounded = round(val, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    # 去尾零:例如 2.50 → "2.5", 2.05 → "2.05"
    s = f"{rounded:.2f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def try_ocr_math_captcha_with_confidence(png_bytes: bytes) -> Optional[dict]:
    """SCRM 数学计算式验证码识别 + 置信度预检 + 安全计算结果。

    关键优化:不再依赖「文本验证码 OCR」(那个函数会用英数正则过滤掉带符号的字符串)。
    直接对 beta=True/False 两个 ddddocr 模型裸识别,每个结果都:
      1) 先走「原流程」: 符号归一化 → 尾部 =? 剥离 → 正则校验 → 计算
      2) 原流程失败 → 再走「强力 fallback」: 穷举多种符号映射假设 × 尾部截断长度,
         计算出任何合理整数结果就算通过。

    Returns:
      None 表示识别/计算任一环节失败(外层应刷新验证码重试)
      dict 结构:
        {
          "ocr_code": str,          # OCR 原始识别结果(归一化前)
          "normalized_expr": str,   # 归一化+去尾部后的纯表达式, 如 "7*0"
          "answer": str,            # 最终计算结果(字符串,直接填输入框), 如 "0"
          "computed_value": int|float,  # 原始计算值
          "used_beta": bool,        # 用的是哪个 OCR 模型
          "min_prob": float,
          "avg_prob": float,
          "probs": list[float],
          "chars": list[str],       # OCR 每个字符(算式)
          "confident": bool,        # 置信度是否通过阈值 AND 计算成功
        }
    """
    best_result: Optional[dict] = None
    # 分高优先:整数 > 近似整数 > 浮点; 置信度高 > 置信度低; beta=True(更准)优先于 beta=False
    best_score = -10_000_000

    for beta in (True, False):
        ocr = get_ocr(beta=beta)
        if ocr is None:
            continue
        # 先尝试 probability=True 模式(拿每个字符概率)
        code_raw: str | None = None
        probs: list[float] = []
        try:
            raw_prob = ocr.classification(png_bytes, probability=True)
            coerced_code, coerced_probs = _coerce_prob_list(raw_prob)
            if isinstance(coerced_code, str) and coerced_code.strip():
                code_raw = coerced_code.strip()
                probs = coerced_probs or []
        except (TypeError, ValueError, Exception):
            pass
        if not code_raw:
            try:
                plain = ocr.classification(png_bytes)
            except Exception:
                continue
            if not isinstance(plain, str) or not plain.strip():
                continue
            code_raw = plain.strip()
            probs = [_OCR_FALLBACK_CONFIDENCE] * len(code_raw)
        # 归一化一下概率长度
        if len(probs) != len(code_raw):
            if len(probs) == 0:
                probs = [_OCR_FALLBACK_CONFIDENCE] * len(code_raw)
            else:
                m = min(len(probs), len(code_raw))
                code_raw = code_raw[:m]
                probs = probs[:m]
        if len(code_raw) < 3:
            continue

        min_p = min(probs) if probs else 0.0
        avg_p = (sum(probs) / len(probs)) if probs else 0.0

        # 收集这个模型下的所有候选项(原流程 + aggressive fallback),选分数最高的
        local_candidates: list[tuple[int, str, float | int, bool]] = []  # (score, expr, computed, is_from_original)

        # === 策略 1: 原「标准流程」(最快,也最准确) ===
        normalized = _normalize_math_captcha_text(code_raw)
        extracted_expr = _extract_math_expr_from_normalized(normalized)
        if extracted_expr:
            computed = _safe_eval_math_expr(extracted_expr)
            if computed is not None and -99999 <= computed <= 99999:
                # 对策略1 候选项也打分(和 fallback 保持一致的评分规则)
                s = _score_math_candidate(extracted_expr, computed, tail_strip=0)
                local_candidates.append((s, extracted_expr, computed, True))

        # === 策略 2: 强力 fallback 穷举 (针对 OCR 把 +/-/=/? 识别成字母/数字) ===
        # 无论策略1 是否成功,都跑一遍,避免策略1给出垃圾答案(如 4+57=61)
        fb = _aggressive_try_math_compute(code_raw)
        if fb is not None:
            s = _score_math_candidate(fb["expr"], fb["answer"], tail_strip=fb.get("tail_strip", 0),
                                      variant_idx=fb.get("variant_idx", 99))
            local_candidates.append((s, fb["expr"], fb["answer"], False))

        if not local_candidates:
            continue  # 这个模型识别不出合法算式,下一个模型

        # 选这个模型下分数最高的候选项
        local_candidates.sort(key=lambda x: x[0], reverse=True)
        top_score, expr_used, computed, _ = local_candidates[0]

        answer_str = _format_math_answer(computed)
        if len(answer_str) > 8:
            continue

        # 评分
        score = 0
        if isinstance(computed, int):
            score += 100_000
        elif float(computed).is_integer():
            score += 50_000
        score += int(avg_p * 10_000)
        score += int(min_p * 1_000)
        # beta=True 是中文混合模型,对中文符号识别更准,略优先
        if beta:
            score += 500

        if score > best_score:
            best_score = score
            ok_range = isinstance(computed, (int, float)) and -99999 <= computed <= 99999
            base_confident = (min_p >= _OCR_MIN_CHAR_PROB) and (avg_p >= _OCR_AVG_PROB)
            final_confident = base_confident and ok_range and isinstance(computed, int)
            best_result = {
                "ocr_code": code_raw,
                "normalized_expr": expr_used,
                "answer": answer_str,
                "computed_value": computed,
                "used_beta": beta,
                "min_prob": round(min_p, 4),
                "avg_prob": round(avg_p, 4),
                "probs": [round(x, 4) for x in probs],
                "chars": list(code_raw),
                "confident": final_confident,
            }

    return best_result


def try_ocr_math_captcha(png_bytes: bytes) -> Optional[str]:
    """简化版:直接返回计算好的验证码答案字符串(填输入框用);失败返回 None"""
    r = try_ocr_math_captcha_with_confidence(png_bytes)
    if r is None:
        if png_bytes:
            try:
                fn = Path(__import__("tempfile").gettempdir()) / f"math_captcha_failed_{uuid.uuid4().hex[:8]}.png"
                fn.write_bytes(png_bytes)
                logger.info(f"[MathCaptchaOCR] 数学验证码识别失败,图片已存: {fn}")
            except Exception:
                pass
        return None
    return r["answer"]

