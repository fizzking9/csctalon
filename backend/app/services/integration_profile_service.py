"""接入配置包模板服务 (#44, A-20260910-002 LOCKED v1.0)

职责:
  1. CRUD: 模板增删改查(管理员)
  2. seed: 幂等灌入 3 个默认场景(**已存在则跳过, 不覆盖用户改动** —— 审计项 C-P2-2)
  3. render: 纯函数变量替换(**只允许预定义白名单**, 非白名单占位符原样保留)

安全要点:
  - 模板 content_md 是管理员可写文本 → 前端**只做纯文本展示 + 复制**, 不 v-html 渲染。
  - 变量替换不走 eval/format(**禁止任意表达式求值**), 只做字面量键值替换。
  - 渲染是纯函数: 无副作用、不落库、不读网络。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration_profile import IntegrationProfile

logger = logging.getLogger(__name__)

# ── 变量白名单(硬约束) ──
# 键 → 说明; 渲染时只替换这些键, 其余 {{xxx}} 原样保留(便于在模板中写文档示例)
ALLOWED_VARIABLES: dict[str, str] = {
    "base_url": "系统对外访问地址",
    "doc_url": "共享文档地址",
    "doc_name": "文档显示名",
    "token": "调用令牌(占位符, 不回填真实明文)",
    "target_type": "目标业务(complaint / manual_refund)",
    "sync_interval": "同步间隔(分钟)",
}

# 目标业务枚举(校验用, 防止模板渲染出非法值)
TARGET_TYPES = ("complaint", "manual_refund")

# 场景枚举
SCENES = ("push", "pull", "manual_refund")

# ── 默认 seed: 3 个场景 ──
# 内容内联在此(migration 亦引用同一常量, 保持单一事实来源)
_SEED_PROFILES: list[dict] = [
    {
        "code": "workbuddy-complaint-push",
        "name": "315/卡商投诉推送",
        "scene": "push",
        "sort_order": 10,
        "variables": [
            {"key": "doc_url", "label": "共享文档地址", "required": True},
            {"key": "doc_name", "label": "文档显示名", "required": False},
            {"key": "sync_interval", "label": "同步间隔(分钟)", "required": False},
        ],
        "content_md": """\
## 任务
定时检查共享文档，内容变化时把投诉明细以 JSON 推送到本系统。

## 输入
- 共享文档地址：{{doc_url}}
- 文档名：{{doc_name}}
- 检查频率：每 {{sync_interval}} 分钟检查一次，**内容变化才推送**（无变化不推送，避免无谓请求）

## 输出（推送到本系统）
- 推送地址：{{base_url}}/api/v1/sync/complaints/push
- 请求方法：POST，`Content-Type: application/json`
- 鉴权：请求头 `Authorization: Bearer {{token}}`

### 请求体
```json
{
  "source_file": "{{doc_name}}.xlsx",
  "external_complaints": [
    {"投诉日期": "2026-08-01", "投诉编号": "...", "投诉人": "张三", "投诉手机号": "...",
     "投诉设备号": "...", "投诉问题": "...", "处理明细": "..."}
  ],
  "card_complaints": [
    {"投诉日期": "2026-08-01", "投诉群": "卡商A群", "投诉人": "李四",
     "设备号": "...", "投诉问题": "...", "处理人": "王五"}
  ]
}
```

## 字段要求
1. 行内字段名直接沿用共享文档的**中文列名**（系统已内置中文列名映射，无需自行转换）
2. `监督局-315投诉` Sheet 的行 → `external_complaints`
3. `卡商群投诉` Sheet 的行 → `card_complaints`
4. **全量推送**该文件内的所有行即可，系统按身份指纹去重，重复推送不会产生重复数据
5. 日期一律 `YYYY-MM-DD` 字符串；金额字段若存在请传数字（元）

## 响应处理
- 成功：`{"code":0,"data":{"status":"success","external":{...},"card":{...}}}`
- `status` = `success` / `partial` / `failed`，非 success 时请记录 `data` 内容
- 返回 401 → token 无效/已吊销，请停止重试并通知管理员
- 返回 503 → 服务端未启用推送通道或已关闭该采集方式，请通知管理员
- 返回 429 → 触发限频，退避后重试

## 约束
1. **不要修改数据内容**：原样传递文档中的行，清洗由本系统负责
2. 不要抽样/只推增量：整份文件推送即可（去重由系统保证）
3. 不要并发推送同一份文件
""",
    },
    {
        "code": "workbuddy-manual-refund-push",
        "name": "商户号手动退款推送",
        "scene": "manual_refund",
        "sort_order": 20,
        "variables": [
            {"key": "doc_url", "label": "共享文档地址", "required": True},
            {"key": "doc_name", "label": "文档显示名", "required": False},
            {"key": "sync_interval", "label": "同步间隔(分钟)", "required": False},
        ],
        "content_md": """\
## 任务
定时检查共享文档，内容变化时把《商户号手机退款处理登记》明细以 JSON 推送到本系统。

## 输入
- 共享文档地址：{{doc_url}}
- 文档名：{{doc_name}}
- 检查频率：每 {{sync_interval}} 分钟检查一次，**内容变化才推送**

## 输出（推送到本系统）
- 推送地址：{{base_url}}/api/v1/sync/manual-refunds/push
- 请求方法：POST，`Content-Type: application/json`
- 鉴权：请求头 `Authorization: Bearer {{token}}`

### 请求体
```json
{
  "source_file": "{{doc_name}}.xlsx",
  "manual_refunds": [
    {"北京登记日期": "2026-08-01", "订单号": "...", "设备号": "...",
     "登记人": "张三", "退款金额": 128.00, "状态": "已退款"}
  ]
}
```

## 字段要求
1. 字段名直接沿用共享文档的**中文列名**
2. **去重键** = `(北京登记日期, 订单号, 设备号, 登记人)`：一笔退款单由这四项确定
3. 金额/状态变更请**重新推送该行**（系统会 UPDATE 同一行，不会新增）
4. **全量对账**可选：请求体加 `"full_sync": true` 时，系统会删除本次未出现的残留行

## 响应处理
- 成功：`{"code":0,"data":{"status":"success","total":...,"inserted":...,"updated":...}}`
- 401 / 429 / 503 处理同投诉推送

## 约束
1. 不要自行计算金额（原样传递文档数值）
2. 不要拆分批次推送同一行（`full_sync` 模式下未出现的行为残留会被删除）
""",
    },
    {
        "code": "workbuddy-daily-brief-pull",
        "name": "经营日报拉取",
        "scene": "pull",
        "sort_order": 30,
        "variables": [],
        "content_md": """\
## 任务
定时从本系统拉取经营数据，生成日报/周报/月报推送给业务方。

## 输入
- 接口前缀：{{base_url}}/api/v1/external
- 鉴权：请求头 `Authorization: Bearer {{token}}`

## 可用端点
| 端点 | 参数 | 说明 |
|---|---|---|
| `GET /brief` | `period=daily` | 一键经营简报（日） |
| `GET /brief` | `period=weekly` | 一键经营简报（本周，周一起） |
| `GET /brief` | `period=monthly` | 一键经营简报（本月） |
| `GET /brief` | `start=&end=` | 任意区间回看（历史标准周等，跨度上限 93 天） |
| `GET /refund` | `date=YYYY-MM-DD` | 当日/指定日退款四路 + 真实现金流 |
| `GET /complaint` | `date=YYYY-MM-DD` | 当日/指定日投诉三源 |
| `GET /reception` | `date=YYYY-MM-DD` | 当日/指定日接待量 |

## 调用示例
```bash
curl -H "Authorization: Bearer {{token}}" \\
  "{{base_url}}/api/v1/external/brief?period=daily"
```

## 数据口径（理解指标用，不要自创定义）
- 退款：四路经营合计 = A 线上直退 + B 钱包取现 + C 闪猫 + D 手动（按创建时间）
- 真实现金流 E：独立不累加，是对账基准
- 投诉三源：内部投诉 / 商户号投诉 / 外部投诉（含 315 与卡商群）
- 金额单位：元（2 位小数）；时间统一北京时间

## 输出要求（提示词）
- 所有数字**必须来自接口返回**，严禁编造、外推或臆测；数据中没有的写「暂无」
- 结论必须注明数据窗口（如「今日」及截至时间）
- 结构建议：结论要点 → 数据明细 → 异常提示
- 回答中文，可用标题/要点/小表格

## 错误处理
| 状态码 | 含义 | 处理 |
|---|---|---|
| 401 | token 无效 / 已吊销 / 已过期 | 停止重试，通知管理员 |
| 403 | 该 token 无此端点 scope | 通知管理员扩权 |
| 429 | 触发限频 | 退避重试 |
| 503 | 外部接口未启用 | 通知管理员 |
""",
    },
]


def seed_profiles(db: Session) -> int:
    """幂等灌入默认模板(已存在则跳过, 不覆盖用户改动)。返回新建条数。"""
    created = 0
    for item in _SEED_PROFILES:
        exists = db.scalar(select(IntegrationProfile).where(IntegrationProfile.code == item["code"]))
        if exists is not None:
            continue  # 不覆盖用户改动(审计项 C-P2-2)
        db.add(IntegrationProfile(
            code=item["code"],
            name=item["name"],
            scene=item["scene"],
            content_md=item["content_md"],
            variables=item.get("variables") or [],
            enabled=True,
            sort_order=item.get("sort_order", 0),
            created_by="system",
        ))
        created += 1
    if created:
        db.commit()
        logger.info("[integration_profile] seed 新建 %d 条默认模板", created)
    return created


def list_profiles(db: Session, *, include_disabled: bool = False) -> list[dict]:
    """列出模板(默认只列启用的)。"""
    stmt = select(IntegrationProfile).order_by(IntegrationProfile.sort_order, IntegrationProfile.id)
    if not include_disabled:
        stmt = stmt.where(IntegrationProfile.enabled.is_(True))
    rows = db.scalars(stmt).all()
    return [to_dict(r) for r in rows]


def get_profile(db: Session, profile_id: int) -> IntegrationProfile | None:
    return db.get(IntegrationProfile, profile_id)


def get_by_code(db: Session, code: str) -> IntegrationProfile | None:
    return db.scalar(select(IntegrationProfile).where(IntegrationProfile.code == code))


def to_dict(row: IntegrationProfile) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "scene": row.scene,
        "content_md": row.content_md,
        "variables": row.variables or [],
        "enabled": row.enabled,
        "sort_order": row.sort_order,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def create_profile(db: Session, payload, actor: str | None = None) -> dict:
    code = (payload.code or "").strip()
    dup = get_by_code(db, code)
    if dup is not None:
        from fastapi import HTTPException
        raise HTTPException(400, f"模板 code 已存在: {code}")
    _validate_scene(payload.scene)
    row = IntegrationProfile(
        code=code,
        name=payload.name,
        scene=payload.scene,
        content_md=payload.content_md,
        variables=[v.model_dump() for v in (payload.variables or [])],
        enabled=True if payload.enabled is None else payload.enabled,
        sort_order=payload.sort_order or 0,
        created_by=actor,
        updated_by=actor,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return to_dict(row)


def update_profile(db: Session, profile_id: int, payload, actor: str | None = None) -> dict | None:
    row = db.get(IntegrationProfile, profile_id)
    if row is None:
        return None
    if payload.name is not None:
        row.name = payload.name
    if payload.scene is not None:
        _validate_scene(payload.scene)
        row.scene = payload.scene
    if payload.content_md is not None:
        row.content_md = payload.content_md
    if payload.variables is not None:
        row.variables = [v.model_dump() if hasattr(v, "model_dump") else v for v in payload.variables]
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    row.updated_by = actor
    db.commit()
    db.refresh(row)
    return to_dict(row)


def delete_profile(db: Session, profile_id: int) -> bool:
    row = db.get(IntegrationProfile, profile_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def reset_profile(db: Session, profile_id: int, actor: str | None = None) -> dict | None:
    """恢复默认: 从 seed 常量读回 content_md(A-20260910-002 §五.1 风险缓解)。

    管理员误改坏模板时可一键还原, 无需手工重写。
    """
    row = db.get(IntegrationProfile, profile_id)
    if row is None:
        return None
    default = next((s for s in _SEED_PROFILES if s["code"] == row.code), None)
    if default is None:
        from fastapi import HTTPException
        raise HTTPException(400, "该模板无内置默认内容(自定义模板不支持恢复)")
    row.content_md = default["content_md"]
    row.variables = default.get("variables") or []
    row.updated_by = actor
    db.commit()
    db.refresh(row)
    return to_dict(row)


def _validate_scene(scene: str) -> None:
    if scene not in SCENES:
        from fastapi import HTTPException
        raise HTTPException(400, f"非法 scene: {scene}; 允许: {', '.join(SCENES)}")


# ─────────────────────────────────────────────────────────────────────
# 渲染(纯函数)
# ─────────────────────────────────────────────────────────────────────
def render_profile(row: IntegrationProfile, values: dict, *, base_url: str = "") -> dict:
    """用给定变量值渲染模板。

    ⚠️ **只替换 ALLOWED_VARIABLES 白名单内的键**; 非白名单 `{{xxx}}` 原样保留
    (便于在模板中写文档示例), 不做 eval / str.format(**任意 dict) 式求值。

    Args:
        row: 模板行
        values: 变量值(键必须属白名单, 否则忽略)
        base_url: 系统对外地址(优先于 values["base_url"])

    Returns:
        {"markdown": str, "char_count": int, "unresolved": [未提供值的必填白名单变量],
         "ignored_keys": [被忽略的非白名单键]}
    """
    effective = {k: v for k, v in (values or {}).items() if k in ALLOWED_VARIABLES}
    if base_url:
        effective["base_url"] = base_url
    ignored = [k for k in (values or {}) if k not in ALLOWED_VARIABLES]

    # target_type 枚举校验(防止渲染出非法值)
    if "target_type" in effective and effective["target_type"] not in TARGET_TYPES:
        from fastapi import HTTPException
        raise HTTPException(400, f"非法 target_type: {effective['target_type']}; 允许: {', '.join(TARGET_TYPES)}")

    text = row.content_md or ""
    # 只做白名单字面量替换(不使用 str.format, 避免模板里的 {} 被当格式串)
    for key in ALLOWED_VARIABLES:
        if key in effective and effective[key] is not None:
            placeholder = "{{" + key + "}}"
            text = text.replace(placeholder, str(effective[key]))

    # 未提供值的白名单变量(仍以占位形态留在文本里)
    unresolved = [
        var["key"] for var in (row.variables or [])
        if var.get("required") and var.get("key") not in effective
    ]

    return {
        "markdown": text,
        "char_count": len(text),
        "unresolved": unresolved,
        "ignored_keys": ignored,
    }
