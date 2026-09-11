"""投诉统计口径 —— 五分类 / 实体级去重 / 状态合并 / 被投诉主体归一 (#47, 2026-09-10)

## 业务口径（用户 2026-09-10 澄清，权威）

1. **工单"投诉"判据 = `service_category` 为空白**
   工单里 `换货/其他/设备故障/套餐疑问/退款/(空白)`，**只有"空白"那个才是投诉**。
   BMS 平台修复后可能会正式显示"投诉" → 判据已预留兼容。

2. **内部投诉（商品/服务）与商户号投诉（微信）不参与外部匹配**
   —— 它们本来就从工单统计，不涉及跨表去重。

3. **只有两类需与工单匹配**：
   - **外部投诉**：工单平台类 + `external_complaints`（315 补充表）
   - **(外)卡商**：`card_complaints`（卡商补充表）

4. **一个投诉可能分多渠道、不同日期**（实测 54 个 SN 跨两张补充表）
   → 统计上**算 1 笔**，但**保留渠道明细**。

## 实测依据（2026-09-10 生产数据）

| 项 | 值 |
|---|---|
| 工单总数 | 265,449 |
| 空白 category（= 投诉） | 32,058 |
| └ 内部投诉（商品 17,148 + 服务 9,696） | 26,844 |
| └ 商户号投诉（微信） | 4,384 |
| └ 外部投诉（工信部 320 + 12315 278 + 公共服务平台 87 + 消费者协会 50） | 735 |
| └ 其他（空 item 81 + 套餐扣费申诉 14） | 95 |
| 315 补充表 | 2,655 |
| 卡商补充表 | 873 |

**交叉验证**：投诉类 `service_item` **100% 落在空白 category 下**，无例外。

## ★工单侧无客户姓名字段（去重键约束）

| 字段 | 实际语义 |
|---|---|
| `registrant` | 内部/系统（`wechat` 26755、`企微-洪佳`） |
| `handler` | 处理人（`企微-董梦玲`） |
| `contact_person` | **100% 空** |
| `submit_params.username` | 客户名，但填充率仅 1.2% |

实测 `SN+手机+姓名` 三者都命中 → **0 匹配** → 去重键定为 **`SN + 手机`**。

## 状态不同步（结构性，非实现问题）

| 项 | 值 |
|---|---|
| 315 已办结 | 2,558 |
| 其中无工单 | **1,566（61%）** |
| 已办结但工单非完成态 | **224（24.9%）** |
| **315 无 `handle_time`** | **79.9%** |

→ 补充表 80% 无处理时间戳，**无法通过同步工单对齐**，
  故采用**状态合并**：任一为完成态即视为已处理。
"""
from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import and_, func, literal, or_
from sqlalchemy import case as sa_case
from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 一、投诉判据（工单）
# ═══════════════════════════════════════════════════════════════════
# 业务口径: `service_category` 空白 = 投诉。
# 兼容 BMS 未来修复(正式输出"投诉"分类) → 追加 OR service_category = '投诉'。
#
# ⚠️ 为什么不用 `ILIKE '%投诉%'`:
#   当前数据下两者结果接近(仅 5 行 category='投诉'), 但**口径来源不同**:
#   - 正确判据是"服务类别未标注"(空白) —— 客服登记投诉时不选类别
#   - `ILIKE` 只是"恰好"命中了 service_item 里的"投诉"字样
#   BMS 修复后若 category 统一填"投诉", ILIKE 仍有 5 行能中, 但空白判据会**全部漏掉**
#   (那时空白行会消失) → 故必须同时支持两种。
_BMS_COMPLAINT_CATEGORY = "投诉"


def is_complaint_ticket_cond(cat_col, item_col=None) -> ColumnElement:
    """工单是否为"投诉"的 SQL 条件

    ★★ 2026-09-11 (#56) 判据收敛 —— 用户指正「不要乱查」 ★★

      用户原话:
      > 「服务类别里为空的 默认都回填为 投诉; 你现在每次都去乱查」

      实测全表 `service_category` 分布(265,449 行):

      | category | 条数 |
      |---|---|
      | 套餐疑问 | 81,638 |
      | 退款 | 65,488 |
      | 其他 | 33,943 |
      | 网络故障 | 32,701 |
      | **空白 = 投诉** | **32,058** |
      | 换货 | 11,320 |
      | 设备故障 | 8,284 |
      | 退货 | 12 |
      | 投诉(显式) | 5 |

      空白 32,058 + 显式 5 = **32,063** —— 与 #53 计算的四类完备数**完全吻合**
      → 判据只需 `category 空白 OR ='投诉'`, **不需要扫 item**。

    ## 参数

      · `cat_col`  —— `service_category`, **唯一判据**
      · `item_col` —— ⚠️ **DEPRECATED(#56)**, 传了会被忽略并记 warning。

        为什么废弃: 底层是 BMS **导出漏洞**(客服登记投诉时不选类别 → 导出成空白)。
        空白本身就是完整判据; 再用 `item ILIKE '%投诉%'` 兜底属于**冗余扫描**,
        且会引入"item 恰好含投诉字样"的偶然命中, 口径来源不纯。
    """
    if item_col is not None:
        logger.warning(
            "is_complaint_ticket_cond 的 item_col 参数已废弃(#56), 本次忽略。"
            "判据只需 service_category 空白或 ='投诉'。"
        )
    return or_(
        cat_col.is_(None),
        func.btrim(cat_col) == "",
        cat_col == _BMS_COMPLAINT_CATEGORY,   # 向前兼容 BMS 修复
    )


def external_ticket_cond(cat_col, item_col, channel_col=None) -> ColumnElement:
    """「**外部投诉类工单**」判据 —— 外部投诉对账的**唯一口径** (#56)

    ★★ 用户 2026-09-11 澄清的权威语义 ★★

      > 「内部投诉(服务项: 商品投诉、服务投诉), 商户号投诉(服务项: 微信投诉)
      >   跟这个外部投诉 **不存在对账关系**。
      >   目前我们外部投诉对账, 主要是对这个**服务类别为空、类型是几个平台的**
      >   外部投诉与文档同步过来的数据对账, 拉齐。
      >   俩边的数据都有其意义: bms 中的服务请求里面的投诉(外部投诉)是**经营登记**;
      >   文档登记的内容是**接待过程 以及人工分析、处理 等等过程数据**。」

    因此外部投诉工单 = **两个条件同时成立**:

      1. 是投诉类工单 (`category` 空白 或 `='投诉'`)
      2. 来源桶 = 外部投诉 (`item` 命中平台关键词)

    ⚠️ 为什么必须加条件 2(而非仅用 SN 匹配):
       实测 918 条已匹配的 315 记录中, **586 条(63.8%)** 挂到了
       `item='未领取体验套餐'/'开发票'/'网慢/网卡/无网络'` 这类
       **非投诉类工单**(category 是 套餐疑问/退款/其他)。
       那些是"同一 SN 的处理过程关联", **不是**投诉经营登记,
       会把「外部投诉 vs 文档」的对账基数污染。

    返回可直接用于 `WHERE` 的条件表达式。
    """
    return and_(
        is_complaint_ticket_cond(cat_col),
        complaint_bucket_case(item_col, channel_col) == BUCKET_EXTERNAL,
    )


# ═══════════════════════════════════════════════════════════════════
# 二、四类桶（服务项 / 渠道 → 投诉分类）
# ═══════════════════════════════════════════════════════════════════
# ⚠️⚠️ 2026-09-11 (#53) 口径二次重构 —— 用户拍板 ⚠️⚠️
#
# 变更 1: **(外)卡商 移出「投诉来源」** —— 它是**完全独立的统计体系**
#   用户原话: "(外部)卡商 的投诉 不纳入总量的管理, 整体独立 统计分析;
#              无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等 全部单算"
#   → 实证依据: 工单表 `service_item`/`service_category` 含「卡商」均为 **0 行**
#     → 卡商在工单侧**不存在独立分类**, 是纯补充表体系(`card_complaints`)
#   → 因此 `ALL_BUCKETS`(投诉来源桶) 只含**三类**: 内部/商户号/外部
#     卡商见 `CARD_SCOPE`(独立体系, 不参与总量)
#
# 变更 2: **四类完备, 移除「其他」桶** —— 用户指出的 BMS 导出漏洞
#   用户原话: "服务工单 里面的 有空白的, 目前回填或者说算做 投诉, 这个是 bms 的
#              导出漏洞; 但是数据是很明显的。另外 对于已知的类型 可以明确定义
#              内部投诉 商户号投诉 和外部投诉; 还有部分 似乎应该归类为 内部投诉"
#   → 实测空白 category 的 item × channel 交叉, **可完全归类无遗漏**:
#       item=商品投诉(17148) + 服务投诉(9696)           → 内部
#       item=消费投诉(空item)+channel=微信投诉(78)      → 商户号  ★channel 救回
#       item=微信投诉(4384)                             → 商户号
#       item=工信部(320)+12315(278)+公共服务平台(87)+消费者协会(50) → 外部
#       item=套餐扣费申诉(14)+空item/渠道H5(3)          → 内部    ★用户所说
#     合计 32,058 ✓ 自洽, **无需「其他」兜底**
#
# 实现优先级(item 优先, channel 回退):
#   1. item 含外部平台关键词 → 外部投诉
#   2. item 含「微信」      → 商户号投诉
#   3. item 含商品/服务投诉 → 内部投诉
#   4. item = 套餐扣费申诉  → 内部投诉
#   5. **channel 含「微信」→ 商户号投诉**   ← 救回 78 行空 item
#   6. else                → 内部投诉      ← 救回 3 行空 item(H5 渠道)
_EXTERNAL_PLATFORM_KEYWORDS = (
    "工信部", "12315", "公共服务平台", "消费者协会", "消协",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)
_MERCHANT_KEYWORDS = ("微信",)
_INTERNAL_KEYWORDS = ("商品投诉", "服务投诉")
# 用户明确「该归内部」的 item
_INTERNAL_ITEM_EXACT = ("套餐扣费申诉",)

# ── 投诉来源桶（**三类**, 卡商不在内）──
BUCKET_INTERNAL = "内部投诉"
BUCKET_MERCHANT = "商户号投诉"
BUCKET_EXTERNAL = "外部投诉"
# ⚠️ 兼容保留: 「其他」桶已在 #53 移除(四类完备)。此常量仅供**历史数据/迁移**识别,
#    新代码不应再产生该值。
BUCKET_OTHER = "其他"

# 投诉来源的全部合法桶（**不含卡商** —— 卡商是独立体系）
ALL_BUCKETS = (BUCKET_INTERNAL, BUCKET_MERCHANT, BUCKET_EXTERNAL)

# ── (外)卡商: 独立统计体系（不参与投诉总量）──
#   用户: "卡商投诉 可以单独于外部投诉, 可以单列 (外)卡商"; 且 #53 进一步要求
#   "不纳入总量的管理, 整体独立统计分析"。
#   ⚠️ 数据来源=`card_complaints` 表(非工单), 口径=表自身行数, **不扣工单重叠**。
CARD_SCOPE = "(外)卡商"
CARD_TABLE = "card_complaints"

# ⚠️ 向后兼容别名: #52 曾用 `BUCKET_CARD` 表示来源桶之一; #53 已把它移出来源桶,
#    但 `resolve_subject` 等函数仍用它区分「卡商主体」语义。保留别名避免大面积改动。
BUCKET_CARD = CARD_SCOPE


def complaint_bucket_case(item_col, channel_col=None) -> ColumnElement:
    """工单 → 投诉来源桶（三类：内部/商户号/外部）

    参数:
      item_col:    `service_item`（主判据）
      channel_col: `channel`（**可选回退**）。给出时, item 无法判定则用渠道兜底
                   —— 可救回 78 行「item 空 + channel=微信投诉」→ 商户号。

    优先级（见上方 #53 说明）:
      外部平台关键词 > 微信 > 商品/服务投诉 > 套餐扣费申诉 > channel微信 > else内部

    ⚠️ **不含卡商** —— 卡商是独立体系(见 `CARD_SCOPE`), 由调用方单独统计。
    ⚠️ **无「其他」兜底** —— #53 实测四类完备, else 兜底为**内部投诉**
       (用户: "还有部分 似乎应该归类为 内部投诉")。
    """
    si = func.coalesce(item_col, literal(""))
    ext_whens = [si.ilike(f"%{k}%") for k in _EXTERNAL_PLATFORM_KEYWORDS]
    mer_whens = [si.ilike(f"%{k}%") for k in _MERCHANT_KEYWORDS]
    int_whens = [si.ilike(f"%{k}%") for k in _INTERNAL_KEYWORDS]
    exact_whens = [si == k for k in _INTERNAL_ITEM_EXACT]

    whens = [
        (or_(*ext_whens), BUCKET_EXTERNAL),
        (or_(*mer_whens), BUCKET_MERCHANT),
        (or_(*int_whens), BUCKET_INTERNAL),
        (or_(*exact_whens), BUCKET_INTERNAL),
    ]
    # channel 回退（仅当调用方提供）
    if channel_col is not None:
        ch = func.coalesce(channel_col, literal(""))
        whens.append((ch.ilike("%微信%"), BUCKET_MERCHANT))
    # else: 内部投诉（#53: 四类完备, 不再兜底「其他」/「外部」）
    return sa_case(*whens, else_=BUCKET_INTERNAL).label("complaint_bucket")


def classify_bucket_from_item(item: str | None, channel: str | None = None) -> str:
    """纯 Python 版分桶（供测试与逐行场景；与 SQL 版同口径）

    优先级: 外部平台词 > 微信 > 商品/服务 > 套餐扣费申诉 > channel微信 > 内部(兜底)

    ⚠️ else 兜底为**内部投诉**（#53）—— 实测空白 category 仅剩
       「空item + H5 渠道」3 行, 用户明确"还有部分 似乎应该归类为 内部投诉"。
    """
    s = (item or "").strip()
    if any(k in s for k in _EXTERNAL_PLATFORM_KEYWORDS):
        return BUCKET_EXTERNAL
    if any(k in s for k in _MERCHANT_KEYWORDS):
        return BUCKET_MERCHANT
    if any(k in s for k in _INTERNAL_KEYWORDS):
        return BUCKET_INTERNAL
    if s in _INTERNAL_ITEM_EXACT:
        return BUCKET_INTERNAL
    if channel and "微信" in str(channel):
        return BUCKET_MERCHANT
    return BUCKET_INTERNAL


# ═══════════════════════════════════════════════════════════════════
# 三、实体级去重
# ═══════════════════════════════════════════════════════════════════
# 用户拍板: 键 = (SN, 手机)，**两者都必须命中**；时间窗 30 天。
#
# ⚠️ 为何不含姓名: 工单侧无客户姓名字段(registrant/handler 都是内部人员,
#    contact_person 100% 空, submit_params.username 填充仅 1.2%)。
#    实测"SN+手机+姓名"三者都命中 → 0 匹配。
#
# ⚠️ 为何要时间窗: 无窗口按 SN 关联会**跨年错配**
#    (实测同一 SN 在不同年份有多次独立投诉, 日期差达 ±356 天; >180 天占 4.7%)。
DEDUP_WINDOW_DAYS = 30

# ═══════════════════════════════════════════════════════════════════
# 投诉**事件**归并参数（#55, 用户 2026-09-11 拍板）
# ═══════════════════════════════════════════════════════════════════
# 「同一投诉·多渠道」与「真·重复」是两个概念，需分别处理：
#   · 同SN+同电话+几天内+**渠道不同** → 同一投诉的多渠道呈现 → 归并1笔，**不算重复**
#   · 同SN+同电话+**渠道也相同**     → 拷贝复制             → 真重复，计1笔
#
# "几天内" 的时间窗：用户举例 9.7→9.10（3天），取 7 天留余量。
EVENT_WINDOW_DAYS = 7
# 严格归并的起始年：用户「2025年之前的数据 简单统计处理就OK，有一定偏差可接受」。
#   → `< EVENT_STRICT_MIN_YEAR` 的年份**统计上不归并**（仅标记 legacy_unmerged）。
EVENT_STRICT_MIN_YEAR = 2026

# 事件类型（供前端/统计区分展示）
EVENT_KIND_SINGLE = "single"                  # 单一记录
EVENT_KIND_MULTI_CHANNEL = "multi_channel"    # 同一投诉·多渠道（非重复）
EVENT_KIND_TRUE_DUP = "true_dup"              # 真·重复（同渠道拷贝复制）
EVENT_KIND_LEGACY = "legacy_unmerged"         # 旧年份，未归并
EVENT_KIND_UNKEYED = "unkeyed"                # 缺 SN/电话，待人工

# 手机号归一: 去非数字 / 去 86 前缀
_PHONE_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(phone: str | None) -> str | None:
    """手机号归一（去空格/括号/横线/国家码），无有效号码则 None

    ⚠️ **多号码字段**：实测存在一个字段填两个号码的情形（用 `/` 分隔）。
       这是**同一投诉的两个联系人**，是有效信息。本函数**只取第一个**作为主号码
       （归并键需单一值），完整号码列表见 `split_phones()`。

    ⚠️ **异常长度**：实测有 10/12/14/19/22 位等脏值（315 15 行、卡商 14 行，占 <1.6%）。
       本函数按"取前 11 位 + 校验号段"处理；无法识别则返回 None（不参与归并，宁可漏合）。
    """
    if not phone:
        return None
    parts = split_phones(phone)
    return parts[0] if parts else None


# 可分号码的分隔符（实测: `/` `,` `、` `或` 空格串）
_PHONE_SPLIT_RE = re.compile(r"[/,，、;；\s]*(?:或|and)[/,，、;；\s]*|[/,，、;；]+|\s{2,}")
# 中国大陆手机号: 1[3-9] + 9 位
_PHONE_11_RE = re.compile(r"1[3-9]\d{9}")


def split_phones(phone: str | None) -> list[str]:
    """从一个字段里拆出**全部合法手机号**（处理"多号码填在一格"的脏数据）

    实测样例:
      · `'1XXXXXXXXXX   /   1YYYYYYYYYY'`（一格填两个号）→ 两个
      · `'+8613800138000'`                 → ['13800138000']
      · `'138-0013-8000'` / `'(138) 0013 8000'` → ['13800138000']  ← 先剥分隔符
      · `'135603166965'` (12位, 首位多 1)  → ['13560316696']（扫描取 11 位）
      · `'/'`、`'abc'`                     → []

    ⚠️ 真实用户手机号不入代码库（gitleaks `hardcoded-phone-number`），
       文档/测试一律用占位或示例号段。

    实现分两步:
      1) 先**常规分隔符**(横线/空格/括号)剥掉, 再按"非数字序列"切段 —— 这样
         `138-0013-8000` 能还原成一整段;
      2) 每段内用 `1[3-9]\\d{9}` **扫描**提取合法号（能容忍粘连/多余位数）。
    """
    if not phone:
        return []
    s = str(phone)
    # 归一国家码前缀(避免 8613... 被误切)
    s = re.sub(r"\+?0{0,2}86(?=1[3-9]\d{9})", "", s)
    # 步骤1: 先剥"号码内部"的常见分隔符(横线/空格/点/括号), 使 138-0013-8000 连成 13800138000
    s = re.sub(r"(?<=\d)[\s\-\.\(\)]+(?=\d)", "", s)
    out: list[str] = []
    # 步骤2: 按"非数字"切成段, 每段内扫描 11 位手机号
    for seg in re.split(r"\D+", s):
        if not seg:
            continue
        if len(seg) == 11 and re.fullmatch(r"1[3-9]\d{9}", seg):
            if seg not in out:
                out.append(seg)
            continue
        # 段过长(如两号粘连)或过短 → 扫描提取全部合法 11 位号
        for m in _PHONE_11_RE.finditer(seg):
            v = m.group(0)
            if v not in out:
                out.append(v)
    return out


def complaint_event_key(sn: str | None, complaint_date) -> tuple[str, str] | None:
    """★投诉**事件**去重键 → (sn, 日期) —— 用户 2026-09-11 拍板口径

    用户原话:
      「把 同天 sn大概率是重复的，比如说 渠道不同的，可以算作重复的，
        当然要记录渠道。」

    语义: **同一设备、同一天** 出现的多条投诉记录 → 视为**同一笔投诉事件**，
    即使来自**不同渠道**(如 全国12315平台 / 航城监所) —— 渠道差异只记录不拆分。

    ⚠️ 与旧键 `complaint_dedup_key(SN, 手机, 30天窗)` 的区别:
       · 旧: 需 SN **和** 手机都命中, 且限 30 天窗 → **漏判**(实测同天同SN的重复被判为 0)
       · 新: **只要 SN + 日期**(同天) → 符合业务直觉, 实测可识别 315 27 组 / 卡商 13 组

    ⚠️ 不含手机: 同一天同一设备可能有多人投诉(实测 315 6/27 组手机不同) ——
       这是**已知边界**, 由 `dedup_events()` 用 `suspect_distinct` 标记待人工判定,
       **不擅自合并**(用户要求"不能乱删…慎重")。

    返回: (sn, 'YYYY-MM-DD')；SN 缺失或日期为空 → None(不参与去重, 避免误合)
    """
    s = (sn or "").strip()
    if not s or complaint_date is None:
        return None
    try:
        d = complaint_date.date() if hasattr(complaint_date, "date") else complaint_date
        return (s, str(d))
    except (AttributeError, TypeError, ValueError):
        return None


def complaint_dedup_key(sn: str | None, phone: str | None) -> tuple[str, str] | None:
    """⚠️ DEPRECATED（#54）—— 旧实体级去重键 `(SN, 手机)`

    已被 `complaint_event_key(SN, 日期)` 取代。保留仅为兼容/参考。

    旧口径问题（实测）: 要求 SN **和** 手机同时命中, 且限 30 天窗 ——
    导致「同天同 SN 但手机为空/不同」的重复**全部漏判**（测得 L2 = 0 组，
    据此一度误判"无真重复"）。
    """
    s = (sn or "").strip()
    p = normalize_phone(phone)
    if not s or not p:
        return None
    return (s, p)


def dedup_events(
    rows: list[dict],
    *,
    sn_key: str = "device_sn",
    date_key: str = "complaint_date",
    phone_key: str = "complainant_phone",
    channel_key: str = "channel",
    id_key: str = "id",
    window_days: int = EVENT_WINDOW_DAYS,
    min_year: int = EVENT_STRICT_MIN_YEAR,
) -> tuple[list[dict], int, list[dict]]:
    """★★ 按用户最终口径（2026-09-11）归并**投诉事件** —— 不删数据, 只做统计侧归并

    ## 用户口径（权威）

    > 「同一个人 设备编号 电话号码 都一致, **几天内** 的投诉,
    >   渠道（平台）是**肯定不同**, 内容略微不同；
    >   可以**归结为一个投诉, 但是不能算重复数据**；
    >   如果 电话 sn 渠道 **都一致** 的话 那就基本上是同一个投诉 **拷贝复制**的,
    >   譬如说 9.7 号的投诉, 但是当天处理没有完结, 拖到 10 号再处理一次。」

    > 「对于 2025 年之前的数据 简单统计处理就 OK, 有一定的偏差或者多重统计可以接受」

    ## 两类"重复"必须区分（我此前混淆, 已纠正）

    | 形态 | 特征 | 归类 | 处理 |
    |---|---|---|---|
    | **同一投诉·多渠道** | 同SN+同电话+**窗外内**+**渠道不同** | `multi_channel` | 归并 1 笔, **不算重复** |
    | **真·重复** | 同SN+同电话+**渠道也相同** | `true_dup` | 计 1 笔, 标 `dup_of` |
    | 单一记录 | 簇内仅 1 条 | `single` | 原样 |

    ## 参数
      · `window_days` —— "几天内" 的时间窗(默认 7 天; 用户举例 9.7→9.10 为 3 天)
      · `min_year`    —— 严格归并的起始年(默认 2026)。**之前的年份统计上不归并**
        (用户: "25年的数据 统计有偏差关系也不大")

    ## 返回
      (events, merged_rows, needs_review)
      · `events`       —— 事件列表, 每条含 `kind`/`channels`/`record_ids`/`count`
      · `merged_rows`  —— 被归并(非首条)的记录数
      · `needs_review` —— 需人工判定的组(电话不同/缺失 → 不自动归并)

    ⚠️ **绝不删除/修改原始行** —— 用户明令"不能乱删除 所以还是慎重"。
       只产出**统计视图**; 明细下钻用 `events[i]["record_ids"]` 回溯原始记录。
    """
    if not rows:
        return [], 0, []

    # ── 1. 按 (SN, 归一化电话) 分组 —— 电话是"实体的人", 用于确认同一人 ──
    buckets: dict[tuple[str, str], list[dict]] = {}
    unkeyed: list[dict] = []
    for r in rows:
        sn = (r.get(sn_key) or "").strip()
        ph = normalize_phone(r.get(phone_key))
        if not sn or not ph:
            unkeyed.append(r)          # 缺 SN 或电话 → 独立成事件(需人工/不自动归并)
        else:
            buckets.setdefault((sn, ph), []).append(r)

    events: list[dict] = []
    merged_rows = 0
    needs_review: list[dict] = []

    def _day(x):
        d = x.get(date_key)
        if d is None:
            return None
        try:
            return d.date() if hasattr(d, "date") else d
        except (AttributeError, TypeError):
            return None

    for (sn, ph), items in buckets.items():
        # 按日期排序(无日期排最后), 用 id 兜底保证稳定
        items = sorted(
            items,
            key=lambda x: (_day(x) is None, _day(x) or date.min,
                           x.get(id_key) is None, x.get(id_key) or 0),
        )

        # ── 2. 时间窗聚类: 与簇首条相差 <= window_days → 同簇 ──
        clusters: list[list[dict]] = []
        for it in items:
            d = _day(it)
            placed = False
            if d is not None:
                for cl in clusters:
                    cd = _day(cl[0])
                    if cd is not None and abs((d - cd).days) <= window_days:
                        cl.append(it)
                        placed = True
                        break
            if not placed:
                clusters.append([it])

        # ── 3. 逐簇判定 ──
        for cl in clusters:
            rep = dict(cl[0])
            chans: list[str] = []
            ch_count: dict[str, int] = {}
            for x in cl:
                c = (x.get(channel_key) or "").strip()
                if c:
                    if c not in chans:
                        chans.append(c)
                    ch_count[c] = ch_count.get(c, 0) + 1

            rep["channels"] = chans
            rep["channel_count"] = len(chans)
            rep["record_ids"] = [x.get(id_key) for x in cl]
            rep["count"] = len(cl)
            rep["multi_channel"] = len(chans) > 1
            rep["complaint_day"] = str(_day(rep) or "")
            rep["event_key"] = f"{sn}|{ph}|{rep['complaint_day']}"

            # 严格归并仅对 min_year 及之后生效(用户: 25年前简单统计)
            yr = _day(rep).year if _day(rep) else None
            strict = (yr is None) or (yr >= min_year)

            if len(cl) == 1:
                rep["kind"] = "single"
                rep["strict_applied"] = strict
            elif not strict:
                # 旧年份: 不归并, 保留原样但标记(供人工查)
                rep["kind"] = "legacy_unmerged"
                rep["strict_applied"] = False
                merged_rows += 0
            elif len(chans) > 1:
                # ★同一投诉的多渠道呈现 → 归并 1 笔, 不算重复
                rep["kind"] = "multi_channel"
                rep["strict_applied"] = True
                merged_rows += len(cl) - 1
            else:
                # ★同渠道多条 → 拷贝复制(真重复)
                rep["kind"] = "true_dup"
                rep["strict_applied"] = True
                dup_ids = [x.get(id_key) for x in cl[1:]]
                rep["dup_of_ids"] = dup_ids
                merged_rows += len(cl) - 1

            events.append(rep)

    # ── 4. 无键行(缺 SN 或电话): 不自动归并, 单独列出待人工 ──
    for r in unkeyed:
        e = dict(r)
        c = (r.get(channel_key) or "").strip()
        e["channels"] = [c] if c else []
        e["channel_count"] = len(e["channels"])
        e["multi_channel"] = False
        e["record_ids"] = [r.get(id_key)]
        e["count"] = 1
        e["event_key"] = None
        e["kind"] = "unkeyed"
        e["strict_applied"] = False
        e["complaint_day"] = str(_day(r) or "")
        events.append(e)

    # ── 5. 需人工判定: 同一 (SN, 同一时间窗) 但电话不同 → 可能是不同人 ──
    #   (用户: "手机不同 默认 统计为不同，但是提供一个人工再审核更合适点")
    by_sn: dict[str, list[dict]] = {}
    for r in rows:
        sn = (r.get(sn_key) or "").strip()
        if sn:
            by_sn.setdefault(sn, []).append(r)
    for sn, items in by_sn.items():
        phones = {normalize_phone(x.get(phone_key)) for x in items}
        phones.discard(None)
        if len(phones) > 1:
            # 按天聚合(同天不同电话 → 更可疑)
            by_day: dict[str, set] = {}
            for x in items:
                p = normalize_phone(x.get(phone_key))
                d = _day(x)
                if p and d:
                    by_day.setdefault(str(d), set()).add(p)
            for d, ps in by_day.items():
                if len(ps) > 1:
                    ids = [x.get(id_key) for x in items if _day(x) and str(_day(x)) == d]
                    needs_review.append({
                        "device_sn": sn,
                        "complaint_day": d,
                        "phones": sorted(ps),
                        "record_ids": ids,
                        "reason": "同设备同日多个电话 → 可能不同人(默认统计为不同)",
                    })

    return events, merged_rows, needs_review


def dedup_rows_by_entity(
    rows: list[dict],
    *,
    sn_key: str = "device_sn",
    phone_key: str = "complainant_phone",
    date_key: str = "complaint_date",
    window_days: int = DEDUP_WINDOW_DAYS,
) -> tuple[list[dict], int]:
    """⚠️ DEPRECATED（#54）—— 旧 `(SN, 手机) + 30天窗` 去重

    已被 `dedup_events(SN, 日期)` 取代。**保留原因**: 供历史对比与兼容既有单测。

    旧口径缺陷（实测）: 判据过严 → 同天同 SN 的重复被判为 0 组。
    新代码请用 `dedup_events()` / `complaint_event_key()`。
    """
    if not rows:
        return rows, 0

    # 键 → 已保留的行列表（用于时间窗比较）
    kept_by_key: dict[tuple[str, str], list[dict]] = {}
    kept: list[dict] = []
    merged = 0

    def _ts(r: dict):
        return r.get(date_key)

    # 先按时间排序（早的优先保留）
    ordered = sorted(rows, key=lambda r: (_ts(r) is None, _ts(r) or ""))
    for r in ordered:
        k = complaint_dedup_key(r.get(sn_key), r.get(phone_key))
        if k is None:
            kept.append(r)
            continue
        group = kept_by_key.setdefault(k, [])
        dup_of = None
        t = _ts(r)
        if t is not None:
            for prev in group:
                pt = _ts(prev)
                if pt is None:
                    continue
                try:
                    if abs((t - pt).days) <= window_days:
                        dup_of = prev.get("id") or prev.get("ticket_no") or prev.get("dedup_hash")
                        break
                except (TypeError, AttributeError):
                    continue
        if dup_of is not None:
            merged += 1
            nr = dict(r)
            nr["dedup_of"] = dup_of          # 明细可回溯"被合并到哪一条"
            kept.append(nr)
        else:
            group.append(r)
            kept.append(r)
    return kept, merged


# ═══════════════════════════════════════════════════════════════════
# 四、状态合并
# ═══════════════════════════════════════════════════════════════════
# 用户拍板: **任一为完成态即已处理**。
# 理由: 79.9% 的 315 记录无 handle_time, 且 61% 已办结记录无工单
#       → 无法通过同步工单状态对齐, 只能"就宽"(任一完成即认为处理过)。
#
# ⚠️⚠️ 集合来源纪律 (2026-09-10, 我在此犯过一次错) ⚠️⚠️
#   我最初**自拟**了一套三源白名单, 实测后被推翻:
#     · 315 侧: 我漏了 `未达成一致方案`(53) / `暂行办理`(45) —— 二者都在
#       `ingest_validator._EXTERNAL_RESULT_SET`(合法枚举)里, 且语义就是"办结"。
#       既有 `complaints._EXTERNAL_DONE_RESULTS`(9 项)同样漏了它们 →
#       实测 done 判定 2,460(旧) vs 2,558(正确) —— **两套既有口径都在漏, 不能照抄**。
#     · 卡商侧: 我按"子串包含"匹配, 双向出错 ——
#       漏判 `已退余额`(3) / `已短信告知`(4) / `已全额退款`(7);
#       误判 `...但设备费用商家**拒绝了**退货退款申请`(含"退款"二字, 实际未解决)。
#   教训: **办结集合必须来自上游合法枚举, 且对"混合语义"字段用前缀锚定而非子串**。

# 工单完成态 —— 单一来源对齐 `complaints._BACKLOG_DONE_SET` / `metric_registry_service._DONE_STATUS_SET`
# (两处既有定义一致; 此处引用同一集合内容, 避免第三处漂移)
TICKET_DONE_STATUSES = (
    "已结案", "已完成", "完成", "已解决", "已回复", "已反馈",
    "已关闭", "终止", "已终止",
)

# 315 办结类 —— 单一来源对齐 `ingest_validator._EXTERNAL_RESULT_SET` 中的**办结语义**子集
#   纳入依据: 上游合法枚举 + 实测该值确为"已办结"终态。
#   排除依据: `需跟进`(16)/`不予受理`(4)/`已提交报告` 不是办结(前者仍在办, 后者只是过程)
EXTERNAL_DONE_RESULTS = (
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
    "投诉无效", "重复投诉", "终止调节", "退回消委会",
    "未达成一致方案", "未达成一致意见", "暂行办理",
)

# 卡商 result —— **混合语义字段**, 必须前缀锚定(见上方教训)。
#
#   实测 873 行 / **117 种**取值, 由两类内容混杂:
#     (a) 短状态枚举(约 546 行, 62.5%): `已退款完成`189 / `已解决`171 / `已电话告知`71 ...
#     (b) **自由文本**(约 207 行): `跟商家多次沟通无果` / `退套餐，退设备，不接受` /
#         `我向客服申请了退款...`
#         —— 这是"消费者要什么 / 沟通过程", **不是**处理结果
#   故**不能**用 `in_(...)` 精确匹配(会漏掉 30+ 种同义变体), 也**不能**用子串包含
#   (会把 `...但设备费用商家**拒绝了**退货退款申请` 判成已解决)。
#
#   本清单**逐前缀核对过真实取值全集**(见 docs/reports/ 审计记录), 只保留
#   "以明确**完成动作**开头"的前缀。**刻意剔除**以下过程动作/诉求:
#     × 已上传凭证(6) / 已提交服告(6) / 已报交报告说明(3)  → 只是提交/上报, 未办结
#     × 已解释说明(1)                                    → 只是解释, 未解决
#     × 退套餐，退设备(,不接受)(2) / 换货(5) / 换设备(1)     → 消费者诉求, 非处理结果
#     × 免费文本(约 207 行)                               → 无前缀可锚定 → 计"未标注"
CARD_DONE_PREFIXES = (
    # 退款/退货类(明确资金动作)
    "已退款", "已退余", "已退差", "已退升级", "退款", "退货退款", "退了",
    # 明确办结
    "已解决",
    # 告知/沟通类(客服已输出处理结论)
    "已电话", "已告知", "已短信", "已联系", "已沟通", "电话加V",
    # 账号/业务动作
    "已注销", "注销", "已开通", "开通直充", "已全额",
    # 补偿类(以补偿结案)
    "已补", "已赠", "补偿",
    # 协商达成
    "同意代充", "店铺同意", "商家已同意", "客服同意",
    # 转派/劝退
    "已分发", "已劝退",
)

STATUS_DONE = "已处理"
STATUS_PENDING = "未处理"
STATUS_UNKNOWN = "未标注"


def _is_done(value: str | None, done_set: tuple[str, ...]) -> bool:
    """精确集合判定（用于工单 status 与 315 result —— 二者取值封闭）"""
    s = (value or "").strip()
    if not s:
        return False
    return s in done_set


def _is_done_by_prefix(value: str | None, prefixes: tuple[str, ...]) -> bool:
    """前缀锚定判定（用于卡商 result —— 混合语义字段, 见上方说明）"""
    s = (value or "").strip()
    if not s:
        return False
    return any(s.startswith(p) for p in prefixes)


def card_result_state(card_result: str | None) -> str:
    """卡商 result 三态归一：已处理 / 未处理 / **未标注**

    ⚠️ 卡商字段是混合语义（117 种取值, 含大量自由文本）→ **必须**保留
       "未标注"第三态, 不能把"无法判定"混进"未处理"（否则积压数虚高、
       且掩盖了上游字段质量问题）。
    """
    s = (card_result or "").strip()
    if not s:
        return STATUS_UNKNOWN
    if _is_done_by_prefix(s, CARD_DONE_PREFIXES):
        return STATUS_DONE
    # 自由文本（超短枚举长度）或明确未办结 → 严格区分
    return STATUS_PENDING


def merge_complaint_status(
    ticket_status: str | None = None,
    external_result: str | None = None,
    card_result: str | None = None,
) -> str:
    """合并状态：**任一为完成态 → 已处理**

    返回 STATUS_DONE / STATUS_PENDING。
    ⚠️ 调用方应同时保留两侧原始值（工单 status / 渠道 result）供展示，
       便于人工核对"依据来自哪一侧"（见 `status_evidence`）。
    """
    if _is_done(ticket_status, TICKET_DONE_STATUSES):
        return STATUS_DONE
    if _is_done(external_result, EXTERNAL_DONE_RESULTS):
        return STATUS_DONE
    if _is_done_by_prefix(card_result, CARD_DONE_PREFIXES):
        return STATUS_DONE
    return STATUS_PENDING


def status_evidence(
    ticket_status: str | None = None,
    external_result: str | None = None,
    card_result: str | None = None,
) -> str | None:
    """返回"已处理"的判定依据（工单/315/卡商），未处理则 None"""
    if _is_done(ticket_status, TICKET_DONE_STATUSES):
        return "工单"
    if _is_done(external_result, EXTERNAL_DONE_RESULTS):
        return "315"
    if _is_done_by_prefix(card_result, CARD_DONE_PREFIXES):
        return "卡商"
    return None


# ═══════════════════════════════════════════════════════════════════
# 五、被投诉主体归一
# ═══════════════════════════════════════════════════════════════════
# 三处来源口径不一（实测）:
#   315  company_entity  50.8%  (云客猫 1069 / 赤霄 97 / 小皮丘 89 / 青晋 67)
#   315  complaint_shop  29.2%  → 但 `complaint_shop` 首选值是 `/`(540),
#        其余是**店铺名**(智锐度/飞狼星/淘系智锐度...), 与 company_entity 同层可比。
#        回退后覆盖率 1353/2655 = 51.0%(其余 1302 行两者都空 → 记为"未标注")
#   卡商 complaint_group 80.3%  ← **语义不纯**: 混入内部群
#   工单 submit_params.complaintCompany  1.2%
#
# 已验证一致性: 工单 complaintCompany = 315 company_entity（样本双方均为"云客猫"）

# 卡商 complaint_group 里的**非被投诉主体**取值 —— 实测 (2026-09-10):
#   `投诉处理内部群`(149) / `航城投诉处理内部群`(20)  → **处理方内部群**, 非主体
#   `消费者委员会`(23) / `市监局投诉群`(2) / `监局投诉处理群`(1)  → **监督/受理渠道**,
#       是投诉被受理的路径, 不是被投诉对象(与 315 的 complaint_shop 同性质)
#   → 用户拍板(2026-09-10): 内部群 → `未标注`; 监督/受理渠道 → **独立的"监督/受理渠道"分类**
#     (不丢信息: 保留"经消委会转办"这一事实, 但不混入被投诉主体排行)
#   ⚠️ 但 `云骏售后群`/`橙果群`/`亿联群`/`日海通群` 等**是真实卡商**的群名
#      → 不能一律用"含'群'就当噪声"的粗暴规则(我最初版本会误杀它们)。
_CARD_INTERNAL_GROUP_PATTERNS = (
    "内部群",        # 投诉处理内部群 / 航城投诉处理内部群
)
# 监督/受理渠道（用户拍板独立分类, 不计入被投诉主体排行）
_CARD_SUPERVISOR_PATTERNS = (
    "委员会",        # 消费者委员会 / 消费者委员会(私)
    "市监局",        # 市监局投诉群
    "监局",          # 监局投诉处理群
)

# 主体归类标签（非被投诉主体时使用）
SUBJECT_UNKNOWN = "未标注"
SUBJECT_SUPERVISOR = "监督/受理渠道"

# 主体名清洗: 去掉尾部"(xxx)"补充说明, 保留主名
_SUBJECT_PAREN_RE = re.compile(r"[（(].*?[)）]")
# 315 complaint_shop 的占位空值
_SUBJECT_PLACEHOLDER = {"/", "-", "--", "无", "N/A", "n/a", "null", "None"}


def is_internal_group(name: str | None) -> bool:
    """卡商 `complaint_group` 是否为**处理方内部群**（非被投诉主体）

    实测: `投诉处理内部群`(149)、`航城投诉处理内部群`(20)
    真实卡商: `云骏售后`(66)、`小皮丘`(39+36)、`金斗云`(30)、`云骏售后群`(11)
    """
    s = (name or "").strip()
    if not s:
        return False
    return any(p in s for p in _CARD_INTERNAL_GROUP_PATTERNS)


def is_supervisor_channel(name: str | None) -> bool:
    """卡商 `complaint_group` 是否为**监督/受理渠道**（消委会/市监局等）

    用户拍板(2026-09-10): 归入独立的"监督/受理渠道"分类, 不计入被投诉主体排行。
    实测: `消费者委员会`(23)、`市监局投诉群`(2)、`监局投诉处理群`(1)
    """
    s = (name or "").strip()
    if not s:
        return False
    return any(p in s for p in _CARD_SUPERVISOR_PATTERNS)


def _parse_complaint_company_from_submit_params(raw: str | None) -> str | None:
    """从工单 submit_params(JSON-ish 文本) 提取 complaintCompany

    ⚠️ 该字段是**非标准 JSON**(key 无引号), 故用正则而非 json.loads
    样本: `{mobile: 1XXXXXXXXXX, remarks: ..., complaintCompany: 云客猫}`
    ⚠️ 手机号已脱敏 —— 真实用户 PII 不入代码库（gitleaks hardcoded-phone-number）
    """
    if not raw:
        return None
    m = re.search(r"complaintCompany\s*:\s*([^,}]+)", str(raw))
    if not m:
        return None
    v = m.group(1).strip().strip("'\"")
    return v or None


def normalize_subject(name: str | None) -> str | None:
    """主体名归一：去占位空值、去括号说明、去首尾空白

    实测占位空值: 315 `complaint_shop` 有 540 行是 `/`、`company_entity` 有 3 行是 `/`
    → 必须当空值处理, 否则会出现"被投诉主体 = /"的脏排行。
    """
    s = (name or "").strip()
    if not s or s in _SUBJECT_PLACEHOLDER:
        return None
    s = _SUBJECT_PAREN_RE.sub("", s).strip()
    # 归一后若只剩分隔符或又变空 → 视为空
    if not s or s in _SUBJECT_PLACEHOLDER or not s.strip("_-&|,，"):
        return None
    return s


def resolve_subject(
    *,
    source_bucket: str,
    company_entity: str | None = None,
    complaint_shop: str | None = None,
    complaint_group: str | None = None,
    submit_params: str | None = None,
) -> str | None:
    """归一"被投诉主体"（只读解析，不加表列）

    315     → company_entity（首选，1069 云客猫）→ complaint_shop（回退，覆盖率 51.0%）
              两者都空或为占位(540 行 `/`) → None（显示"未标注"，实测 1302/2655）
    卡商    → complaint_group；内部群 → None（"未标注"）；
              **监督/受理渠道 → SUBJECT_SUPERVISOR 标签**（用户拍板独立分类）
    工单    → submit_params.complaintCompany（填充率 1.2%，尽力而为）
    """
    if source_bucket == BUCKET_CARD:
        if is_internal_group(complaint_group):
            return None
        if is_supervisor_channel(complaint_group):
            return SUBJECT_SUPERVISOR
        return normalize_subject(complaint_group)
    if source_bucket == BUCKET_EXTERNAL:
        return normalize_subject(company_entity) or normalize_subject(complaint_shop)
    # 工单侧
    return normalize_subject(_parse_complaint_company_from_submit_params(submit_params))
