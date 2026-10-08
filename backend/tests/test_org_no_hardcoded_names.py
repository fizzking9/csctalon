"""组织架构「硬编码人名」守护测试 (#90)

## 为什么要这个测试

生产事故（2026-09-14，用户反馈）：「明明有清晰的负责人、组长划分，
为什么会出现唐彩绘是负责人、董梦玲是负责人的说法？是不是有硬编码？」

**确实是硬编码**：`scripts/init_groups_specialties.py` 曾把专项负责人
写死成人名：

```python
specialty_leaders = {
    "complaint": "张蕾",
    "refund_recovery": "唐彩慧",   # 用户已明确「他不是 lead」
    "package": "朱海洋",
    "consulting": "董梦玲",
}
```

该脚本**无幂等守卫**，每次执行都会把 `specialties.current_lead_id` 重置回
硬编码值，把用户正当的轮岗决策覆盖掉。最终表现为「负责人说法与组织架构
认知不一致」，且排查成本极高（改完以为好了，再跑一次脚本又坏）。

## 本测试锁定两条不变式

1. **初始化脚本中不得出现任何人名字面量** —— 人名是业务数据，不是代码常量；
2. **初始化脚本必须幂等** —— 已有 current lead 时不得覆盖
   （否则任何「初始化/重跑」都会静默回退生产数据）。

纪律来源：`AGENTS.md` 「禁止无意图改动」+ 「存量不追溯，改动即补齐」。
测试失败时的正确修法是**删除硬编码、改为数据驱动**，
而不是把断言里的名字换掉。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "archive"

# 已知的组长/主管/专员姓名（用于检测硬编码）。
# ⚠️ 本测试自身的名字列表只用于「探测」，不参与业务逻辑 —— 这是允许的；
#    业务代码里出现这些名字才是问题。
HARDCODE_FORBIDDEN_NAMES = [
    "叶珍凤", "董梦玲", "吴晓龙", "黄亚康", "洪佳",
    "朱海洋", "张蕾", "唐彩慧", "刘景山", "陈卓",
    "苏紫依", "邵晨雨", "田兴月", "陈玉红",
]


def _read(name: str) -> str:
    p = SCRIPTS_DIR / name
    if not p.exists():
        pytest.skip(f"{name} 不存在")
    return p.read_text(encoding="utf-8")


def _strip_docstrings(text: str) -> str:
    """剥掉三引号块（其内是人名**说明文案**，不算硬编码）"""
    import re
    return re.sub(r'""".*?"""', '""', text, flags=re.S)


def _code_lines(text: str, keep_line_numbers: bool = True):
    """产出「纯可执行代码」行（去掉注释与 docstring）。

    说明性引用（用户决策原文、变更原因）都在注释/文案里，应当豁免；
    真正的问题是**把人名当业务键值/查询条件**写进可执行代码。
    """
    body = _strip_docstrings(text)
    out = []
    for lineno, line in enumerate(body.splitlines(), start=1):
        s = line.strip()
        if s.startswith("#"):
            continue
        code = line.split("#", 1)[0]
        if not code.strip():
            continue
        out.append((lineno, code) if keep_line_numbers else code)
    return out


@pytest.mark.parametrize("script", ["init_groups_specialties.py"])
def test_init_script_has_no_hardcoded_person_names(script: str):
    """**初始化脚本**的可执行代码里不得出现人名（#90 根因）。

    为什么只对 init 脚本严格要求：
      - init 脚本是**可重跑**的（无幂等语义时尤其危险）→ 硬编码会反复覆盖生产；
      - `apply_org_change_20260914.py` 是**一次性变更脚本**，其人名是本次
        变更的**指令输入**（"把张蕾标记为离职"这种动作必然要点名），
        属合理用法 —— 对它只检查「多值覆盖」类缺陷（见下方专项测试）。
    """
    text = _read(script)
    offenders = []
    for lineno, code in _code_lines(text):
        for nm in HARDCODE_FORBIDDEN_NAMES:
            if nm in code:
                offenders.append((lineno, nm, code.strip()[:90]))

    assert not offenders, (
        f"{script} 中检测到**可执行代码**里的人名硬编码（#90 根因）：\n"
        + "\n".join(f"  L{ln}: [{nm}] {code}" for ln, nm, code in offenders)
        + "\n\n修法：删除硬编码，改为数据驱动（读 current_lead_id / 从 DB 推导）。"
        "\n人名只能出现在注释或说明文案里，不能作为业务键值。"
    )


def test_init_script_has_no_hardcoded_group_names():
    """初始化脚本不得硬编码**组名**（组名是可变业务数据）。

    回归: 曾有 `if "张蕾组" in groups_data:` 块把组名+人名同时写死；
    2026-09-14 组改名后该分支成为死代码，且同类组名一旦重建会强行改组长。
    """
    text = _read("init_groups_specialties.py")
    # 组名模式: 「人名 + 组」
    pattern = re.compile(r'["\']([\u4e00-\u9fa5]{2,4}组)["\']')
    offenders = []
    for lineno, code in _code_lines(text):
        for m in pattern.finditer(code):
            offenders.append((lineno, m.group(1), code.strip()[:90]))

    assert not offenders, (
        "init_groups_specialties.py 中检测到硬编码组名（#90）：\n"
        + "\n".join(f"  L{ln}: [{g}] {code}" for ln, g, code in offenders)
        + "\n\n修法：组名从 `Person.group_name` 数据推导，不要在代码里写死。"
    )


def test_init_script_is_idempotent_for_existing_lead():
    """初始化脚本必须**幂等**：已有 current lead 时不得覆盖。

    回归: 原实现无守卫 → 每次重跑都把 `refund_recovery.current_lead_id`
    重置为硬编码的「唐彩慧」，覆盖用户「吴晓龙负责退款挽回」的决策。
    """
    text = _read("init_groups_specialties.py")
    assert re.search(r"if\s+s\.current_lead_id\s*:", text), (
        "init_groups_specialties.py 缺少 `if s.current_lead_id:` 守卫 —— "
        "重跑会覆盖已有负责人（#90）"
    )


def test_init_script_handles_ambiguous_candidates_without_guessing():
    """候选不唯一时必须**留空**而非猜一个。

    #90 的教训：猜错会让「谁是负责人」出现与用户认知相悖的说法。
    吴晓龙负责退款挽回/套餐，但他**不是这两个专项的成员** ——
    任何"按专项内管理岗推断"的算法都推不出他，此时必须留空交人工指派。
    """
    text = _read("init_groups_specialties.py")
    assert "留空" in text, (
        "init_groups_specialties.py 在候选歧义时应留空并提示人工指派，"
        "而不是任选一个（#90）"
    )


def test_apply_org_change_uses_all_not_scalar_for_lead_migration():
    """`apply_org_change` 的 lead 迁移必须用 `.all()` 而非 `scalar()`。

    回归: `scalar()` 在存在多条 current 时行为未定义（抛错或静默取一条），
    导致旧 lead 错失清理、重复行永久遗留（#90 生产实测同一人 2 条 current）。
    """
    text = _read("apply_org_change_20260914.py")
    start = text.index("def step_specialty_leads")
    end = text.find("\ndef ", start + 1)
    body = text[start:end if end > 0 else len(text)]

    assert ".scalars(" in body and ".all()" in body, (
        "step_specialty_leads 必须用 `.scalars(...).all()` 取全部 current lead —— "
        "`scalar()` 在多条 current 时会漏清（#90）"
    )


def test_migration_adds_partial_unique_index():
    """必须存在**部分唯一索引**迁移，从数据库层杜绝重复 current（#90 根治）。

    仅靠应用层守卫不够：生产已实测同一 (specialty, person, role) 出现 2 条
    current，说明应用层确实漏过。
    """
    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    found = False
    for f in versions.glob("*.py"):
        t = f.read_text(encoding="utf-8")
        if "uq_slh_spec_role_current" in t and "WHERE is_current" in t:
            found = True
            break
    assert found, (
        "未找到「部分唯一索引」迁移 —— 必须在 specialty_leadership_history "
        "(specialty_id, role) WHERE is_current 上加 UNIQUE（#90）"
    )
