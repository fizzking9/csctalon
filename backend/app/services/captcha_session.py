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


# 合法验证码校验: 闪猫/BMS 验证码常见组合为「中文汉字 + 大写/小写字母 + 数字」混合,
# 不区分字符集(ddddocr 可能识别出中文数字/中文),长度通常 3-8 位,放宽到 3-12 位兜底。
# 常见字符集:
#   - 英数: [A-Za-z0-9]
#   - 中文: 常用汉字区 \u4e00-\u9fff(含中文数字零一二三四五六七八九十百千等)
#   - 允许中划线 / 下划线? 少见,暂不引入;识别出再扩展。
_CAPTCHA_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9]{3,12}$")


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
                import uuid
                import tempfile
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
