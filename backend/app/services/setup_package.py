"""外部接入配置包生成 (A-20260910-003 #43 LOCKED v1.3)

背景: 调用方(WorkBuddy 等)需要四样东西才能接入 —— token + 接口地址 + 提示词 +
规范说明, 此前只能人工四处拼凑。本模块把它们拼成一整块 Markdown, 一次复制即可。

设计要点:
  1. **纯函数**: 无副作用、不落库、不读网络; 输入 token 行 + base_url → 输出 Markdown。
  2. **按 scope 裁剪**: 只列该 token 实际有权限的端点与提示词段, 不误导调用方。
  3. **明文绝不出现**: Markdown 中 token 位置固定为占位符 `<在此粘贴你的 token>`;
     配置包**不是**绕过 hash 存储的明文后门(见 A-20260910-003 §2.2)。
  4. **端点表与 SCOPES 同源**: `scope → 端点` 映射集中在此, 测试断言与
     `external_service.SCOPES` 完全一致, 防止新增 scope 时漏更新配置包。
  5. 模板可被 `integration_profiles` 表覆盖(#44), 未配置则用本模块内置常量。
"""

from __future__ import annotations

import logging

from app.models.external_token import ExternalAccessToken

logger = logging.getLogger(__name__)

# token 明文占位符(配置包中一律用它, 绝不回填真实明文)
TOKEN_PLACEHOLDER = "<在此粘贴你的 token>"

# 对外统一口径常量(与 docs/standards/外部只读数据接入规范.md 同源维护)
API_PREFIX = "/api/v1/external"
AMOUNT_UNIT_NOTE = "元(2 位小数), 不做分/元换算"
TIMEZONE_NOTE = "统一北京时间 Asia/Shanghai"

# ── scope → 端点清单(单一事实来源; 测试断言与 S.SCOPES 一致) ──
SCOPE_ENDPOINTS: dict[str, list[dict[str, str]]] = {
    "reception": [
        {
            "method": "GET",
            "path": "/reception",
            "query": "date=YYYY-MM-DD",
            "summary": "当日/指定日接待量(缺省=今天实时)",
        }
    ],
    "refund": [
        {
            "method": "GET",
            "path": "/refund",
            "query": "date=YYYY-MM-DD",
            "summary": "当日/指定日退款四路经营合计 + 真实现金流 E",
        }
    ],
    "complaint": [
        {
            "method": "GET",
            "path": "/complaint",
            "query": "date=YYYY-MM-DD",
            "summary": "当日/指定日投诉(三源: 内部/商户号/外部)",
        }
    ],
    "brief": [
        {
            "method": "GET",
            "path": "/brief",
            "query": "period=daily|weekly|monthly",
            "summary": "一键经营简报(接待专项/退款四路+涉诉率/投诉三源)",
        },
        {
            "method": "GET",
            "path": "/brief",
            "query": "start=YYYY-MM-DD&end=YYYY-MM-DD",
            "summary": "任意区间回看(历史标准周/指定整天; 单次跨度上限 93 天)",
        },
    ],
}

# ── scope → 提示词段(给调用方的大模型用) ──
SCOPE_PROMPTS: dict[str, str] = {
    "reception": (
        "接待口径: 接待量 = 客服处理记录数(按 operation_time 落当日); "
        "device_count = 当日服务的去重设备 SN 数; yoy_* 为与昨日同时间区间的对比。"
    ),
    "refund": (
        "退款口径: refund_count/refund_amount 为四路经营合计(A 线上直退 + B 钱包取现 + "
        "C 闪猫 + D 手动, 按创建时间); refund_done_today 为路线 A 当日实际完成(按更新时间); "
        "actual_cashflow 为真实现金流 E(对账基准, 独立不累加)。"
    ),
    "complaint": (
        "投诉口径: 三源 = 内部投诉 / 商户号投诉 / 外部投诉(含 315 与卡商群); "
        "外部投诉桶 = 工单外部投诉类 + external_complaints + card_complaints。"
    ),
    "brief": (
        "简报口径: 一次返回接待专项 + 退款四路(含 A 路涉诉率) + 投诉三源(登记/处理/积压); "
        "优先用 brief 端点取数, 避免多次拼装导致口径漂移。"
    ),
}

# ── 输出要求(通用提示词, 恒带) ──
_OUTPUT_REQUIREMENTS = """\
- 所有数字**必须来自接口返回**, 严禁编造、外推或臆测; 数据中没有的写"暂无/未提供"
- 严格按上述口径理解指标, 不要自创定义
- 结论必须注明数据窗口(如"今日""本周""本月"及截至时间)
- 金额一律以元为单位
- 结构建议: 结论要点 → 数据明细 → 异常提示
- 回答中文, 可用标题/要点/小表格"""

# ── 错误码表(通用) ──
_ERROR_TABLE = """\
| 状态码 | 含义 | 处理 |
|---|---|---|
| 401 | token 无效 / 已吊销 / 已过期 | 联系系统管理员 |
| 403 | 该 token 无此端点所需 scope | 联系系统管理员扩权 |
| 429 | 触发频率限制 | 退避重试, 不要高频重试 |
| 503 | 外部接口未启用 | 联系系统管理员 |"""


def _rate_limit_text(row: ExternalAccessToken, global_default: int) -> str:
    """按 token 实际限频配置渲染(三值语义), 避免写死误导调用方。

    v1.2 三值: 0=不限制 / NULL=全局默认 / >0=个性化。
    """
    val = row.rate_limit_per_min
    if val == 0:
        return "不限制"
    if val is None:
        return f"全局默认({global_default} 次/分钟)"
    return f"{val} 次/分钟"


def _scope_display(scopes: list[str]) -> str:
    """scope 中文说明拼接。"""
    names = {
        "reception": "接待",
        "refund": "退款",
        "complaint": "投诉",
        "brief": "经营简报",
    }
    return " / ".join(f"{s}({names.get(s, s)})" for s in scopes)


def build_setup_package(
    row: ExternalAccessToken,
    base_url: str,
    *,
    global_rate_limit: int = 60,
) -> dict:
    """生成接入配置包(纯函数)。

    Args:
        row: token 行(只读使用, 不修改)
        base_url: 系统对外访问地址(如 https://csctalon.example.com)
        global_rate_limit: 全局默认限频(用于 NULL 的渲染)

    Returns:
        {
          "markdown": str,          # 完整配置包(可直接复制给调用方)
          "char_count": int,
          "endpoints": [...],       # 该 token 可用端点清单(结构化, 前端可展示)
          "scopes": [...],
        }

    ⚠️ 返回值**不包含** token 明文 —— 明文位置一律用 TOKEN_PLACEHOLDER 占位。
    """
    scopes = list(row.scopes or [])
    base_url = (base_url or "").rstrip("/")

    # 端点清单: 按 scope 顺序去重聚合(保持 SCOPE_ENDPOINTS 的声明顺序)
    endpoints: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for scope in scopes:
        for ep in SCOPE_ENDPOINTS.get(scope, []):
            key = (ep["path"], ep["query"])
            if key in seen:
                continue
            seen.add(key)
            endpoints.append({**ep, "scope": scope})

    # 提示词段: 只取该 token 有权限的 scope 口径
    prompt_lines = [SCOPE_PROMPTS[s] for s in scopes if s in SCOPE_PROMPTS]

    ep_table = "\n".join(
        f"| `{ep['method']} {API_PREFIX}{ep['path']}` | {ep['query']} | {ep['scope']} | {ep['summary']} |"
        for ep in endpoints
    ) or "| — | — | — | 该 token 未配置任何可用端点, 请联系管理员 |"

    # 示例: 取第一个端点(优先 brief, 因为它最常用)
    example_ep = next((e for e in endpoints if e["scope"] == "brief"), endpoints[0] if endpoints else None)
    if example_ep:
        query = example_ep["query"].split("|")[0] if example_ep["query"].startswith("period") else ""
        example_url = f"{base_url}{API_PREFIX}{example_ep['path']}" + (f"?{query}" if query else "")
        example_block = (
            f'curl -H "Authorization: Bearer {TOKEN_PLACEHOLDER}" \\\n'
            f'  "{example_url}"'
        )
    else:
        example_block = "(该 token 无可用端点)"

    md = f"""\
## 任务：拉取本系统经营数据生成报告

## 一、接口信息

| 项目 | 值 |
|---|---|
| 接口前缀 | `{base_url}{API_PREFIX}` |
| 鉴权方式 | HTTP 请求头 `Authorization: Bearer {TOKEN_PLACEHOLDER}` |
| 数据格式 | `application/json`, 成功统一返回 `{{"code":0,"data":...,"message":"ok"}}` |
| 金额单位 | {AMOUNT_UNIT_NOTE} |
| 时间口径 | {TIMEZONE_NOTE} |
| 该 token 权限 | {_scope_display(scopes)} |
| 该 token 限频 | {_rate_limit_text(row, global_rate_limit)} |
| 只读性 | 所有端点只读, 不会写入或修改业务数据 |

> **请把上方 `{TOKEN_PLACEHOLDER}` 替换为系统管理员分发给你的一串令牌**, 仅需替换这一次。

## 二、可用端点

| 端点 | 参数 | scope | 说明 |
|---|---|---|---|
{ep_table}

## 三、调用示例

```bash
{example_block}
```

## 四、数据口径（理解指标用, 不要自创定义）

{chr(10).join(f"- {line}" for line in prompt_lines) if prompt_lines else "- (该 token 无口径说明)"}

## 五、输出要求

{_OUTPUT_REQUIREMENTS}

## 六、错误处理

{_ERROR_TABLE}
"""

    return {
        "markdown": md,
        "char_count": len(md),
        "endpoints": endpoints,
        "scopes": scopes,
    }
