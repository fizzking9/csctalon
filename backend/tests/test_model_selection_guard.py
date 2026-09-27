"""模型选型约束测试 —— 防止「降级到已知会算错的模型」再次出现

## 为什么需要这个文件

同一个问题（`specialist` 档位把 Dense 模型降级到 MoE `coder-30b`，
导致数值任务**静默算错**）在评审中**被反复提出多次**，每次都当作"新发现"。

根因不是没修，而是**修了没锁**：这条约束散落在
`TIERS` 常量、DB `llm_role_members`、talon 的 `TALON_CAPABILITIES` /
`TALON_MODEL_FALLBACKS` 4 处，且没有任何测试断言"不该出现 coder-30b"。
于是每次读代码都会重新发现同一问题。

本文件把**实测能力矩阵**编码为可执行断言 —— 让约束在 CI 里失败，
而不是在评审里被人工发现。

## 依据（实测，见 docs/reports/模型能力矩阵与任务分流-*.md）

| 模型 | 架构 | 数值聚合 | 相对时间解析 | 非数值 |
|---|---|---|---|---|
| qwen3.8:27b | Dense | ✅ 4/4 | ⚠️ 4/5 | ✅ |
| qwen3.5:122b | Dense | ✅ 4/4 | ? | ✅ |
| qwen3-coder:30b | **MoE** | ❌ **3/4**（偏差 20000） | ❌ **1/5** | ✅ 快 3x |
| qwen2.5:7b | Dense | ❌（语义匹配 5/5 全错） | ? | ⚠️ |

## 核心约束

**涉及数值的档位，不可把候选降级到 MoE `coder-30b`** ——
「静默算错」比「直接失败」更糟：调用方拿到的是格式合法但数字错的报告，
无任何异常信号。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app.services import llm_tiers  # noqa: E402

# 实测已知：数值聚合会静默算错的模型（不可作为涉数值档位的候选）
NUMERIC_UNSAFE = {"talon-gmk-qwen3-coder-30b", "talon-gmk-qwen3-coder:30b",
                  "qwen3-coder:30b", "qwen3-coder-30b"}

# 用途含数值/结构化输出的档位 —— 其候选必须数值安全
# `specialist` 的 desc 明确写了「结构化输出 / 治理研判」，二者都可能涉及数值
NUMERIC_SENSITIVE_TIERS = ("specialist", "expert")


@pytest.mark.parametrize("tier", NUMERIC_SENSITIVE_TIERS)
def test_specialist_candidates_are_numeric_safe(tier):
    """**核心回归**: 涉数值档位的候选不可含 coder-30b

    这条断言存在的唯一目的就是让问题**无法再次潜入** ——
    测试失败时应去查能力矩阵，而不是改测试。
    """
    t = llm_tiers.TIERS.get(tier)
    assert t, f"档位 {tier} 未定义"
    for slot in ("primary", "secondary"):
        code = t.get(slot)
        assert code, f"{tier}.{slot} 未配置"
        assert code not in NUMERIC_UNSAFE, (
            f"{tier}.{slot} = {code} —— 该模型实测数值聚合 3/4（偏差 20000）、"
            f"相对时间解析 1/5，而 {tier} 用途含结构化输出/治理研判。\n"
            f"降级会**静默算错**（格式合法但数字错），比失败更糟。\n"
            f"应改为 Dense 模型（如 qwen3.5:122b，数值聚合 4/4）。"
        )


def test_specialist_secondary_is_122b():
    """锁定当前选型：specialist 备选是 122b（Dense，数值聚合 4/4）"""
    assert llm_tiers.TIERS["specialist"]["secondary"] == "talon-gmk-qwen3.5-122b"


def test_assistant_may_use_coder():
    """对照: assistant 用途是「分类打标/语义匹配/摘取排版」—— 不涉数值,
    故 coder-30b 作为首选是**实测支持的正确选型**（语义匹配 6/6、快 3x）。

    本测试防止「矫枉过正」—— 不能因为 coder 数值不行就把它从所有地方删掉。
    """
    assert llm_tiers.TIERS["assistant"]["primary"] == "talon-gmk-qwen3-coder-30b"


def test_no_numeric_sensitive_tier_references_coder_in_source():
    """**源码级扫描**: 4 处定义里都不能把 coder-30b 配给 specialist

    为什么扫源码而不只测运行值: 该约束**散落在 4 处**
    (csctalon TIERS / DB llm_role_members / talon CAPABILITIES / talon MODEL_FALLBACKS),
    只测一处会漏。源码扫描能在**改配置的那一刻**就拦住。

    ⚠️ 只扫「赋值语句」, 不扫注释与文档字符串 —— 能力矩阵与 rationale
       里**本该**提到 coder-30b（那是解释为什么不用它）。
    """
    import re
    from pathlib import Path

    # 向上找仓库根(含 csctalon/backend 或 talon 兄弟目录);
    # CI 容器把 backend 挂为 /app, 路径深度不足且无兄弟仓库 → skip 而非 IndexError
    root = None
    for p in Path(__file__).resolve().parents:
        if (p / "csctalon/backend").exists() or (p / "talon").exists():
            root = p
            break
    if root is None:
        pytest.skip("CI 容器布局(/app)无 csctalon/talon 兄弟目录, 跨仓源码扫描不适用")
    offenders = []

    patterns = [
        # talon: TALON_CAPABILITIES=...;specialist=<models>;...
        (root / "talon/services/talon-api/app/config.py",
         re.compile(r"specialist=([^;\"\n]+)")),
        # csctalon: "specialist": { ... "secondary": "...", }
        (root / "csctalon/backend/app/services/llm_tiers.py",
         re.compile(r'"secondary":\s*"([^"]+)"')),
    ]

    for path, pat in patterns:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # 去掉注释行与三引号字符串, 只看可执行赋值
        code_lines = []
        in_doc = False
        for line in text.splitlines():
            s = line.strip()
            if s.count('"""') == 1:
                in_doc = not in_doc
                continue
            if in_doc or s.startswith("#"):
                continue
            code_lines.append(line)
        code = "\n".join(code_lines)

        for m in pat.finditer(code):
            val = m.group(1)
            if "coder" in val and "specialist" in code[max(0, m.start()-80):m.start()]:
                offenders.append(f"{path.name}: {m.group(0)}")

    if (root / "talon/services/talon-api/app/config.py").exists():
        cfg = (root / "talon/services/talon-api/app/config.py").read_text(encoding="utf-8")
        # capabilities 默认值里 specialist 的成员
        m = re.search(r'capabilities:\s*str\s*=\s*"([^"]*)"', cfg)
        if m and "specialist" in m.group(1):
            seg = m.group(1).split("specialist=")[1].split(";")[0]
            if "coder" in seg:
                offenders.append(f"talon config.py capabilities 默认值: specialist={seg}")

    assert not offenders, (
        "涉数值档位 specialist 的候选中出现 coder-30b:\n  "
        + "\n  ".join(offenders)
        + "\n该模型数值聚合实测 3/4（偏差 20000）→ 降级会静默算错。"
    )
