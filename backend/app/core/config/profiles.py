# -*- coding: utf-8 -*-
"""运行模式 Profile —— 环境差异的**单一真值来源**。

## 为什么需要这个模块

2026-09-22 复查发现：项目**只有"模式变量"、没有"模式机制"**。
`settings.ENV` 全仓仅 2 处引用（微信 mock、错误措辞），**不驱动任何环境差异**。

后果（两次同因事故）：
- 2026-09-10：测试连到开发库 → `refunds 170960→2`
- 2026-09-22：脚本差点连到真实库执行 `DROP DATABASE`（纯属运气未遂）

根因不是"配置没分离"，而是**行为不由模式决定**：
库名、.env 路径、日志级别、数据目录全部各自独立取值，
靠"此刻 shell/CWD 里恰好有什么变量"决定 → 错误无法被察觉。

本模块把「模式 → 全部环境差异」收敛为**一个函数、一处定义**。

## 用法

    from app.core.config.profiles import resolve_profile
    p = resolve_profile("dev")
    p.db_name          # "csctalon_dev"
    p.log_level        # "DEBUG"

## 铁律

**新增任何"dev 与 prod 行为不同"的地方，必须加进 `Profile` 字段，
不允许在别处写 `if ENV == "prod"`。**
—— 否则又回到"模式不驱动行为"的老路（同 A-20260910-004 双份定义漂移教训）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["dev", "test", "prod"]

# 合法模式集合（供校验与错误提示复用，避免多处硬编码字面量）
VALID_MODES: tuple[str, ...] = ("dev", "test", "prod")

# 默认模式 = dev。
# ★ 为什么默认 dev 而非 prod：安全侧原则。
#   若默认 prod，忘记设 APP_MODE 的脚本会连真实库；
#   默认 dev 时忘记设置只是连不上开发库 —— 失败可见且无破坏性。
DEFAULT_MODE: Mode = "dev"


@dataclass(frozen=True)
class Profile:
    """一种运行模式的**全部**环境差异。

    frozen=True：profile 是常量，运行期不得被改写（防止某处偷偷 `p.db_name = "csctalon"`）。
    """

    mode: Mode

    # ── 数据库 ──
    # db_name: 该模式**期望**连接的库名。启动守卫据此断言（见 guard.py）。
    db_name: str
    # db_name_patterns: 该模式允许的库名特征（用于守卫匹配）。
    #   dev  → 必须以 _dev 结尾（或显式豁免，见 allow_real_db）
    #   test → 必须以 _test 结尾
    #   prod → 不得以 _dev/_test 结尾
    required_db_suffix: str | None
    forbidden_db_suffixes: tuple[str, ...]

    # ── 配置文件 ──
    # env_file: 该模式加载的 .env 文件（None = 不加载文件，全靠环境变量）。
    #   ⚠️ 这是修掉「按 CWD 解析 .env」的关键：
    #      原实现 SettingsConfigDict(env_file=".env") 是 CWD 相对的，
    #      从仓库根跑会加载根目录那份 ENV=prod 的 .env → 加载到生产配置。
    #      改为 profile 指定**绝对路径**后，行为不再随 CWD 漂移。
    env_file: str | None

    # ── 日志 ──
    log_level: str
    log_format: Literal["detailed", "plain", "json"]
    # 是否输出 SQL（生产强制 False——SQL 日志含真实数据，且量极大）
    sql_echo: bool

    # ── 数据目录（相对 backend/）──
    data_dir: str
    upload_dir: str
    # log_dir: 持久化日志目录；None = 只输出到 stdout（不写文件）。
    #   ★ dev/test 为 None：避免按 CWD 创建 ./logs（原实现只在容器写 /app/logs，
    #     本地本就静默失败）—— 保持"仅 prod 落文件"的原有意图，
    #     也避免为日志重新引入 CWD 依赖（本机制的一大忌）。
    log_dir: str | None

    # ── 行为开关 ──
    # 是否暴露 /docs /redoc（生产按需关闭）
    docs_enabled: bool
    # 是否允许「连到的库不是本模式期望的库」继续运行。
    # 生产恒 False（prod 必须连 prod 库）；dev/test 也恒 False（见 guard 设计），
    # 保留该字段是为了让「豁免」成为**显式**行为而非散落的 if。
    allow_db_mismatch: bool
    # 是否允许对**真实库**执行破坏性操作（scripts/ 的 --allow-real-db 会读它）。
    # prod 为 True（生产运维脚本本就该操作生产库）；
    # dev/test 为 False —— 即使目标是真实库也拒绝，除非 profile 被显式覆盖。
    allow_destructive_on_real_db: bool

    # ── 前端 ──
    # 传给前端的环境标识（VITE_APP_MODE）。与 VITE_BUILD_MODE（菜单开关）**语义分离**：
    #   前者 = 环境（决定 API 地址、日志上报等）
    #   后者 = 构建档位（决定显示哪些菜单）
    vite_app_mode: str

    def describe(self) -> str:
        """单行摘要 —— 供启动日志输出，让"当前跑在哪个环境"一目了然。"""
        return (
            f"mode={self.mode} db={self.db_name} log={self.log_level}/{self.log_format} "
            f"docs={'on' if self.docs_enabled else 'off'}"
        )


# ── 三种 profile 的定义（**唯一真值来源**）──

_DEV = Profile(
    mode="dev",
    db_name="csctalon_dev",
    required_db_suffix="_dev",
    forbidden_db_suffixes=(),
    env_file=".env.dev",
    log_level="DEBUG",
    log_format="detailed",     # 含代码位置，便于本地排障
    sql_echo=False,            # 默认关（需要时 .env.dev 里开 SQL_ECHO=true）
    data_dir="./data",
    upload_dir="./uploads",
    log_dir=None,              # 本地只输出到 stdout（见字段注释）
    docs_enabled=True,         # 本地开发需要 /docs
    allow_db_mismatch=False,
    allow_destructive_on_real_db=False,   # ★ dev 不得动真实库
    vite_app_mode="development",
)

_TEST = Profile(
    mode="test",
    db_name="csctalon_test",
    required_db_suffix="_test",
    forbidden_db_suffixes=(),
    env_file=None,             # 测试不用 .env 文件，全靠 conftest 注入（见 conftest 说明）
    log_level="WARNING",       # 测试期压噪音，让失败信息突出
    log_format="plain",
    sql_echo=False,
    data_dir="/tmp/csctalon-test",
    upload_dir="/tmp/csctalon-test/uploads",
    log_dir=None,              # 测试期不写文件，压噪音
    docs_enabled=False,
    allow_db_mismatch=False,
    allow_destructive_on_real_db=False,   # ★ test 绝不得动真实库（#42 事故根因）
    vite_app_mode="test",
)

_PROD = Profile(
    mode="prod",
    db_name="csctalon",                     # ★ 生产库名保持不变（2026-09-22 决策）
    required_db_suffix=None,
    forbidden_db_suffixes=("_dev", "_test"),  # ★ prod 连到 dev/test 库 → 拒绝启动
    env_file=".env.prod",
    log_level="INFO",
    log_format="json",         # 结构化：便于采集与告警
    sql_echo=False,            # 强制关
    data_dir="/data",
    upload_dir="/app/uploads",
    log_dir="/app/logs",
    docs_enabled=False,
    allow_db_mismatch=False,
    allow_destructive_on_real_db=True,    # 生产运维脚本本就操作生产库
    vite_app_mode="production",
)

_PROFILES: dict[str, Profile] = {
    "dev": _DEV,
    "test": _TEST,
    "prod": _PROD,
}


def resolve_profile(mode: str | None) -> Profile:
    """模式 → Profile。

    未知模式**报错而非静默降级**：拼错 `APP_MODE=prdo` 时若悄悄退回 dev，
    会让本该连生产库的进程连到开发库（或反之）—— 正是本次要消灭的失败模式。
    """
    resolved = (mode or DEFAULT_MODE).strip().lower()
    if resolved not in _PROFILES:
        raise ValueError(
            f"未知运行模式 {mode!r}（有效值: {', '.join(VALID_MODES)}）。\n"
            f"  提示: 请检查环境变量 APP_MODE / ENV。\n"
            f"  ⚠️ 此处**故意不降级** —— 静默退回默认模式会造成"
            f"「以为连 A 实际连 B」，正是本机制要消除的隐患。"
        )
    return _PROFILES[resolved]


def all_profiles() -> dict[str, Profile]:
    """返回全部 profile（供测试与文档生成使用）。"""
    return dict(_PROFILES)
