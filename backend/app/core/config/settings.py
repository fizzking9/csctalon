"""应用配置 - 由运行模式 Profile 驱动, 按模式加载 .env

## 2026-09-22 改造要点（详见 docs/arch/A-20260922-001-运行模式框架与配置分离.md）

原实现的问题**不是"没有配置文件"，而是"模式不驱动任何行为"**：
  · `settings.ENV` 全仓仅 2 处引用（微信 mock、错误措辞），不决定库名/日志/目录
  · `env_file=".env"` 按 **CWD** 解析 —— 从仓库根跑会加载根目录那份 `ENV=prod` 的
    `.env`，于是同一条命令在不同目录连**不同的库**
  · `POSTGRES_DB` 默认值就是真实库名 `csctalon` → "忘记配置" = "连真实库"

后果是**错误不可见**：本地与生产的库名都是 `csctalon`，
连错库时配置/日志/界面没有任何异常信号（2026-09-10 造成 `refunds 170960→2`，
2026-09-22 同因复发）。

本模块现由 `profiles.resolve_profile()` 驱动，并把三类错配变为**启动期硬失败**。
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .guard import check_db_matches_profile
from .profiles import Profile, resolve_profile

# 生产环境禁止使用的弱默认值(fail-fast: 漏配 .env 即裸奔)
_PROD_FORBIDDEN_DEFAULTS = {
    "POSTGRES_PASSWORD": "csctalon123",
    "JWT_SECRET_KEY": "CHANGE_ME_IN_PRODUCTION_PLEASE_USE_A_LONG_RANDOM_STRING",
    "INIT_ADMIN_PASSWORD": "admin123",
    "REDIS_PASSWORD": "",
}

# ★ 库名的"不可用占位符"（2026-09-22）。
#   为什么不沿用 "csctalon"：那会使"忘记配置"等价于"连真实库"。
#   改为占位符后，忘记配置 → 启动期报错（可见、无破坏性）。
#   这不是"多加一道校验"，而是把默认值从**危险**改为**安全侧**。
_PLACEHOLDER_DB = "__MUST_SET_POSTGRES_DB__"

# backend/ 目录（本文件位于 backend/app/core/config/settings.py）
_BACKEND_DIR = Path(__file__).resolve().parents[3]


def _resolve_mode() -> str:
    """解析运行模式：`APP_MODE` > `ENV`（兼容旧变量）> 默认 `dev`。

    ★ 命名演进：旧名 `ENV` 易与"环境变量"混淆，且与 shell 的 `$ENV` 重名。
      新名 `APP_MODE` 语义明确。`ENV` 保留为兼容别名 ——
      生产 `.env` 里仍是 `ENV=prod`，直接废弃会让升级瞬间失配。
    """
    return os.environ.get("APP_MODE") or os.environ.get("ENV") or "dev"


def _resolve_env_files(profile: Profile) -> tuple[str, ...]:
    """按 profile 解析要加载的 .env（绝对路径元组）。

    ★ 为什么用绝对路径：原 `env_file=".env"` 是 CWD 相对的，从仓库根跑会加载
      根目录那份 `ENV=prod` 的 .env → **加载到生产配置**。改后行为不随 CWD 漂移。

    加载顺序（pydantic-settings 语义：**后者优先级更高**）：
      1) 通用 `.env`   —— 共享默认值（可选）
      2) 模式专属文件   —— profile.env_file（`.env.dev` / `.env.prod`）
      3) 环境变量       —— 最高优先级（部署注入，覆盖文件）

    ⚠️ 兼容性兜底：模式专属文件**不存在**时回退用通用 `.env` ——
      让存量部署（只有 `.env`）平滑升级，否则生产会因"找不到 .env.prod"失去全部配置。
        · 生产：compose 通过 `env_file:` 把配置注入**环境变量**，容器内本无
          `/app/.env`（实测确认）→ 回退分支不影响生产。
        · 本地：`backend/.env` 继续生效，无需立刻改名。
    """
    files: list[str] = []
    generic = _BACKEND_DIR / ".env"
    if generic.exists():
        files.append(str(generic))

    if profile.env_file:
        specific = _BACKEND_DIR / profile.env_file
        if specific.exists():
            files.append(str(specific))
        elif not files:
            files.append(str(generic))
    return tuple(files)


# 模式在**模块导入期**解析一次。
#   为什么不在函数里动态取：model_config 需要静态值；且"进程启动时模式固定"
#   是合理约束（不允许运行期切库）。
_PROFILE: Profile = resolve_profile(_resolve_mode())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_files(_PROFILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "数据分析工具应用"
    APP_VERSION: str = "0.1.0"
    # 运行模式: dev / test / prod —— 由 profile 驱动(不再各自为政)。
    ENV: Literal["dev", "prod", "test"] = _PROFILE.mode
    DEBUG: bool = True
    # SQLAlchemy SQL 语句打印(engine echo)。默认关闭——即使 DEBUG=true 也不全量打 SQL,
    # 避免 SQL 噪音淹没真正错误、撑大日志。需要排障时显式设 SQL_ECHO=true。
    # ⚠️ profile 为 prod 时下方 validator 强制置 False（SQL 日志含真实数据且量极大）。
    SQL_ECHO: bool = _PROFILE.sql_echo

    # 网站备案信息(网站备案要求展示, 空则前端显示占位)
    # ICP 备案号, 如 "京ICP备12345678号-1"
    ICP_BEIAN_NO: str = ""
    # 公安备案号, 如 "京公网安备 11010502000000号"
    PSB_BEIAN_NO: str = ""
    # 备案备注/网站性质说明, 如 "个人开发数据分析系统应用测试，内部数据和流程测试"
    BEIAN_NOTE: str = ""
    # 网站版权主体
    COPYRIGHT_HOLDER: str = "tanzk"
    # 网站副标题
    SITE_SUBTITLE: str = "专注数据分析与内部工具"

    # 数据库
    POSTGRES_USER: str = "csctalon"
    # ★★ 2026-09-22 关键变更 ★★
    #   原默认值 `"csctalon"` **就是真实库名** → "忘记配置" 等价于 "连真实库"。
    #   配合"从仓库根跑会加载根目录 ENV=prod 的 .env"，`python -c` 误连真实库
    #   几乎是必然(2026-09-22 未遂事故)。
    #   现改为占位符：忘记配置 → 启动期报错（可见、无破坏性）。
    #   显式配置途径（优先级低→高）：
    #     · `.env` / `.env.dev` / `.env.prod` 文件
    #     · 环境变量 POSTGRES_DB（生产 compose 走这条，实测容器内有该变量）
    POSTGRES_DB: str = _PLACEHOLDER_DB
    POSTGRES_PASSWORD: str = "csctalon123"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: str = "5432"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: str = "6379"
    REDIS_DB: str = "0"
    REDIS_PASSWORD: str = ""  # 生产必填(fail-fast 校验); 为空则连 redis 无密码

    # JWT
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_PLEASE_USE_A_LONG_RANDOM_STRING"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1天

    # 敏感配置加密 key(用于 sync_source_configs 凭证加密);留空则从 JWT_SECRET_KEY 派生
    CONFIG_ENCRYPTION_KEY: str = ""

    # ── 跨站免密入口：csctalon 管理员 → netmgr 网络管理面板（工单 #187）────────
    # 本系统是**签发方**：管理员点入口 → 用共享密钥签 60 秒一次性 HMAC 票据 →
    # 浏览器跳 netmgr `/sso/land#ticket=…`，由 netmgr 验签并建立自己的会话。
    # 票据放 URL fragment（不发给服务端），故不进 access log、不随 Referer 外泄。
    # 留空 = 免密入口关闭（不影响本系统自身登录）。
    # ⚠️ 与 netmgr 侧 `SSO_SHARED_SECRET` 必须逐字符一致。
    SSO_SHARED_SECRET: str = ""
    # netmgr 面板对外地址（HTTPS）。本地/ZT 调试可临时改 http://10.147.17.34:6080。
    SSO_NETMGR_URL: str = "https://panel.kimirock.top"
    # 票据有效期（秒）。须与 netmgr 侧 TTL_SECONDS 一致，勿随意调大。
    SSO_TICKET_TTL_SECONDS: int = 60
    # talon 算力通道管理台直链（仅前端放链接，走 talon 自身可信网段免密，不签票）。
    # talon 没有公网入口，故给两个候选地址，由前端按当前访问来源选一个：
    #   _LAN：办公局域网地址（如 http://192.168.10.21:4080）
    #   _ZT ：ZeroTier 地址（如 http://10.147.17.22:4080）
    # 两者都留空 = 前端不显示该入口。
    # ⚠️ 刻意不把它暴露到公网：talon 鉴权按**对端 IP** 判可信网段，任何反代/隧道都会让源 IP
    #    变成代理机，一旦代理机落在 TALON_TRUSTED_ADMIN_NETWORKS 内即等于对全网免密（工单 #188）。
    SSO_TALON_URL_LAN: str = ""
    SSO_TALON_URL_ZT: str = ""

    # ── 跨子域 SSO（反向：netmgr 面板 → 本系统）─────────────────────────
    # 方向：**本系统是消费方**，netmgr 面板（panel.kimirock.top）是身份源。
    #   面板管理员已登录 → 面板签 60 秒 HMAC 一次性票据 → 302 到本系统 /api/v1/auth/sso
    #   → 本系统验票 → 复用自身登录链路签 JWT → 引导页写 localStorage 跳转。
    # 与上方 SSO_SHARED_SECRET（本系统→面板方向）是**两个独立密钥**，不可混用。
    # ⚠️ NETMGR_SSO_SECRET 必须与 netmgr 侧 /opt/netmgr/.env 的 SSO_CSCTALON_SECRET 逐字符一致。
    NETMGR_SSO_SECRET: str = ""
    # 面板 SSO 签票入口（无票/票据过期时回跳此地址重新签票）
    NETMGR_SSO_PANEL_URL: str = "https://panel.kimirock.top/sso/csctalon"
    # 票据 sub 映射的本系统账号（面板固定签此用户名，须与本系统已存在且 active 的账号一致）
    NETMGR_SSO_DEFAULT_USER: str = "admin"

    # 上传
    # ★ 2026-09-22: 默认值由 profile 决定（dev=./uploads / test=/tmp/... / prod=/app/uploads）
    #   使"数据写到哪"也受模式驱动，而非散落的字面量。
    UPLOAD_DIR: str = _PROFILE.upload_dir
    MAX_UPLOAD_SIZE_MB: int = 200
    # Playwright 登录态保存目录(独立于 UPLOAD_DIR, 不被 /uploads 静态挂载暴露)
    # ★ 由 profile 的 data_dir 派生 —— 这正是 `data_dir` 字段的**接线点**
    #   （原先该字段声明了但没人读，属"空承诺"）。
    BROWSER_STATE_DIR: str = f"{_PROFILE.data_dir}/browser_states"

    # BMS 平台(定时同步用,手动同步时前端传入)
    BMS_USERNAME: str = ""
    BMS_PASSWORD: str = ""
    BMS_SCHEDULE_AUTOSTART: bool = True  # 启动时是否自动开启定时同步(默认开启,否则跨日/重启后整小时窗口都漏)
    # ── 日志/审计保留期(#261 B4-5, 夜批 purge_expired_logs 用) ──
    # operation_logs(HTTP 层操作日志)保留天数, 超期由夜批批删
    OPERATION_LOG_RETENTION_DAYS: int = 180
    # audit_changes(统一变更审计)保留天数 —— 审计价值更高, 保留更久
    AUDIT_CHANGE_RETENTION_DAYS: int = 730
    # 低频源每日定时档(时)(A-20260907-001 LOCKED / 工单#31): 每天该整点对
    # 「DB default_sync=true 且非高频 4 源」的大表/报表源跑"前一天整天"。
    # 默认 6:00(避开 5:00 缺口清点与 8:00 全量); 置空/负值可禁用低频档。
    LOW_FREQ_HOUR: int = 6

    # ── 采集链路可靠性(A-20260904-002 LOCKED / 工单#2 3.1 错误分级重试) ──
    # 分片级重试: 单片瞬时失败(5xx/JSON500/超时)的最大重试次数
    AI_ACC_RETRY_MAX: int = 3
    # 指数退避基线与上限(秒); 实际 sleep = min(base * 2**attempt, cap) * jitter(±20%)
    AI_ACC_RETRY_BASE_S: float = 1.0
    AI_ACC_RETRY_CAP_S: float = 30.0
    # 尊重上游 Retry-After 头时, 额外等待上限(秒)
    AI_ACC_RETRY_AFTER_CAP_S: float = 60.0
    # 整窗(>1天)失败后按日降级重试的最大日片数兜底(架构 3.1 按日降级)
    AI_ACC_DAY_DEGRADE_MAX_DAYS: int = 7

    # ── 熔断优化(A-20260904-002 LOCKED / 工单#2 3.2) ──
    # 滑动窗口熔断: 同一业务日同一源连续失败 >= N 次(6h 滑动窗口内)才熔断,
    # 替代"单轮失败即熔断一整天"。偶发单次失败(如 9/2 15:00)不再直接熔断。
    CIRCUIT_FAIL_THRESHOLD: int = 3
    # 滑动窗口(秒): 计数 TTL, 窗口内连续失败达阈值才熔断
    CIRCUIT_WINDOW_SECONDS: int = 6 * 3600
    # 半开探测间隔(秒): 熔断后每 X 秒放一次单片探测, 成功即摘除熔断自动续采
    CIRCUIT_HALF_OPEN_INTERVAL_SECONDS: int = 2 * 3600

    # ── 文档采集失败快速重试(R-P1-16 #241, 2026-10-09) ──────────────────
    # doc_sync_sources / doc_sync_configs 上一轮失败时, 下一轮调度等待的**上限**(分钟):
    # 实际失败等待 = min(源 interval, 本上限)。默认 30 = 对齐 BMS 高频槽距, 使 60min
    # 档源失败后约在下一个高频槽位即重试(旧实现失败也刷 last_sync_at, 要静默等满
    # 整个 interval)。20min 档源不会被拖慢(min(20,30)=20)。
    DOC_FAILED_RETRY_CAP_MINUTES: int = 30

    # SCRM 微信聚合平台(对接客服接待评估报表)
    # 说明:默认种子值仅用于首次启动灌入 SyncSourceConfig(加密),实际以 DB 配置为准
    SCRM_BASE_URL: str = "https://gateway.bilinl.com"  # 平台 OpenAPI 基础地址
    SCRM_CLIENT_ID: str = ""  # ClientId(对应 SyncSourceConfig.username=Fernet(clientId))
    SCRM_CLIENT_SECRET: str = ""  # ClientSecret(对应 SyncSourceConfig.password=Fernet(secret))
    # 白音 SCRM 后台 Playwright 网页登录凭证(手机号/密码) —— 仅 fix 脚本灌入用,运行时从 DB 读
    # ⚠️ 严禁硬编码进脚本/入库; 通过环境变量或 .env 注入
    SCRM_WEB_USERNAME: str = ""
    SCRM_WEB_PASSWORD: str = ""

    # 微信扫码登录(2026-08-20 审计 H-1/SEC-03):
    # 未配置 WECHAT_APPID/WECHAT_SECRET 时, 仅当本开关=true 且 ENV != prod 才允许 mock openid;
    # 生产环境必须配置真实微信凭证, 否则 code-login/register 返回 503(拒绝降级)。
    WECHAT_MOCK_ALLOWED: bool = False

    # workbuddy 投诉数据推送鉴权 token(315投诉/卡商投诉同步)。
    # 为空时同步接口返回 503;请配置强随机串并告知 workbuddy 以 Authorization: Bearer <token> 调用。
    WORKBUDDY_SYNC_TOKEN: str = ""

    # ── 外部只读开放平台(见 docs/arch/A-20260902-021 LOCKED) ──
    # 是否启用 /api/v1/external 只读接口。false 时外部端点一律 503(默认关闭, 需显式开启)。
    EXTERNAL_API_ENABLED: bool = False
    # 全局默认限频(次/分钟): token 未配 rate_limit_per_min 时使用(每半小时 1 次绰绰有余)。
    EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN: int = 60
    # brief 单次 start/end 任意区间允许的最大跨度(天)。护栏: 防一次拉太宽历史窗口
    # 压库/被当全量导出工具滥用。默认 93 天(≈1 季度, 覆盖季度简报/回看)。end 传未来日期
    # (晚于今天)固定拒绝 400(不随本配置放宽, 防时间穿越)。单日端点(reception/refund/complaint)
    # 仅收单日 date, 不受此限制。
    EXTERNAL_BRIEF_MAX_RANGE_DAYS: int = 93

    # ── LLM / AI 分析助手(见 docs/arch/A-20260904-003 LOCKED v1.0) ──
    # 是否启用 /api/v1/ai 助手。false 时 ai 端点一律 503(默认关闭, 需显式开启)。
    LLM_ENABLED: bool = False
    # OpenAI 兼容完整前缀(是否含 /v1 以服务商为准, llm_client 统一拼 /chat/completions):
    #   DeepSeek 官方 = https://api.deepseek.com (不带 /v1)
    #   本地 Ollama = http://127.0.0.1:11434/v1
    # ⚠️ 工单 #108: 此项为「.env 单源模式」兜底; DB 多 Provider 模式(llm_providers 表)
    #   优先, 库为空时才走 .env 单源。建议同时配置(库 provider 全失败时仍可兜底)。
    LLM_BASE_URL: str = ""
    # API key(DeepSeek 等云端必填); 经环境变量注入, 严禁硬编码/入库/进日志。本地无鉴权服务可留空。
    LLM_API_KEY: str = ""
    # 模型名(一期=deepseek-v4-flash; 二期切本地私有化如 qwen2.5:27b, 仅改本配置)。
    LLM_MODEL: str = ""
    # DeepSeek 思考模式(取数问答建议 disabled 或 low 以省 token/降延迟): disabled/low/high。
    LLM_THINKING: str = "disabled"
    # 生成 read 超时(秒); 云端/本地生成可能数十秒, 单独放宽。
    LLM_TIMEOUT_S: float = 120.0
    # 瞬时失败(5xx/429/超时)指数退避重试。
    LLM_RETRY_MAX: int = 2
    LLM_RETRY_BASE_S: float = 1.0
    LLM_RETRY_CAP_S: float = 30.0

    # ── 数据出域管控（2026-09-21 新增，见 docs/design/20260921-模型角色分组与出域管控设计.md）──
    # 背景: 云端 provider(deepseek-v4-flash) 原先硬编码为所有档位的兜底项，
    #   本地模型 transient 失败(实测 27b 曾 30% 超时) 会**静默把 prompt 发往公网**，
    #   而 prompt 内含真实客服工单数据。这与「数据不出内网」的核心定位冲突。
    # 策略取值:
    #   never         = 云端**完全不可用**（从所有调用链中移除）
    #   explicit_only = 仅当调用方显式指定云端时才用（默认，兼顾体验与合规）
    #   auto          = 本地失败自动降级云端（改造前的旧行为，需管理员显式选择）
    LLM_CLOUD_POLICY: str = "explicit_only"
    # 喂给模型的数据底座(feed)长度上限(字符), 防 token 超限与延迟。
    # by_specialty 已精简(2026-09-04), 正常 feed <3KB; 此值作兜底防超长。
    # #98 (2026-09-15): 12000→6000。生产实测 llm_ms/total_ms=85~95%(#93 埋点),
    #   输入 token 直接推高首 token 延迟; 层2 上线后按 scope 切段 feed 更小。
    # #114 (2026-09-15): 6000→4000→3500。配合 max_tokens=500 + system_prompt 长度约束
    #   (回答≤600字) 让模型自然完成在 ~400 tokens 不截断; 3500 数据 + 920 system +
    #   926 glossary + 1776 focus ≈ 3000 prompt_tokens, prefill 12s + eval 13s = ~28s。
    AI_ANALYZE_MAX_CHARS: int = 3500
    # AI 任务结果在内存保留时间(秒)与历史条数(单 worker 内存态)。
    AI_TASK_TTL_S: int = 600
    AI_TASK_HISTORY: int = 20
    # AI 单次回答最大输出 token(防超长生成拖慢响应); analyze 首查同步等待上限(秒)。
    # #98 (2026-09-15): 1500→800。生产实测 out_chars 多在 1200-2000(对应 800 token),
    #   且 out_chars 与 llm_ms 强正相关(2308字符→6484ms; 171字符→1479ms),
    #   降输出 token 上限直接砍 llm_ms 大头。
    # #114 (2026-09-15): 800→500。qwen3.8:27b eval 31 tok/s, 800→500 省 9.7s;
    #   500 token≈1200 字符中文报告, 实测足够(典型业务报告 800-1500 字符)。
    # #117 (2026-09-16): 500→800。#114 的 500 上限导致 LLM 想"长说"时被强制截断
    #   (生产 ai_reports 表 id=1/3 completion_tokens=500 达顶, md_len 775/706 末尾不自然)。
    #   实测 800 后 completion_tokens=598, md_len=914, 结尾自然收束; total_ms=46.7s
    #   (微涨可接受), 不破坏 LLM client 60s timeout 上限。
    # ⚠️ 2026-09-21 由 800 调回 1500。
    #    原 #98 为了控制耗时把 1500→800，但实测 ai_analyze 的输出**恰好被截在 800**
    #    （done_reason='length'，输出被腰斩），而该任务的典型输出是 1000+ token
    #    （实测被截样本 prompt 6025 chars / out 1034 chars）。
    #    即：这个上限比任务的正常输出**还小** → 每次都截断。
    # 降上限换耗时应通过 max_tokens 之外的手段（如缩短 prompt / 换快模型），
    # 而不是把上限压到低于正常输出 —— 那会让结果**静默不完整**。
    # R-P1-4 (#240, 2026-10-09) 口径声明: 本 1500 是**前台实时问答的简洁档**, 不属于
    #   R3《算力深度评估》定义的「详尽长报告」场景, 故不适用「长报告 ≥4000」结论;
    #   详尽周/月/滚动长报告走后台预生成(AiReportTemplate.max_tokens 按 period 分档
    #   2500/4000/5000), 两条路径独立, 不得互相照搬。
    AI_MAX_TOKENS: int = 1500
    # ── AI_SYNC_WAIT_S (#98 修订 #93, 2026-09-15) ───────────────────────
    # analyze 首查「同步等待」上限：超过此秒数未出结果则改回 task_id 走前端轮询。
    #
    # #93 (2026-09-14) 曾把 6.0→3.0, 理由是"p50=5.05s/p90=6.13s, 6.0 落中位与p90之间
    #   26.7%越线, 降到3.0让大家更快去轮询"。但该决策有问题：
    #   p50=5.05s > 3.0 → 降到3.0后**几乎100%越线走轮询**, 体感反而更差
    #   (前端要额外RTT, 用户先等3s无果再轮询)。
    #
    # #98 改为 8.0：层1 降 AI_MAX_TOKENS(1500→800)+AI_ANALYZE_MAX_CHARS(12000→6000)后,
    #   预计 total_ms 从 ~5s 降到 ~2.5s, p90 ~3s < 8.0 → 90%+ 一次返回不走轮询。
    #   "一次返回"对主动等结果的 AI 场景体验最佳(省轮询RTT与"先等后轮"的双段体感)。
    #
    # 瓶颈定位（沿用 #93 埋点，未变）：
    #   build_ms 417~3042, llm_ms 1479~6893, llm_ms/total_ms = 85~95% → 模型生成是大头。
    #   #98 的层1 降输入/输出 token 才是治 llm_ms, 提同步等待只是减少轮询体感。
    #
    # 越线不等于失败 —— 只是"改走轮询"，任务仍在后台执行；#89 已修复 404 死循环。
    # 保留可调：改 .env 的 AI_SYNC_WAIT_S 即可回退，无需改代码。
    AI_SYNC_WAIT_S: float = 8.0

    # ── AI 预生成「无新事件跳过」三档开关 (#193/#204, 2026-10-07) ─────────
    # 设计: docs/design/20260924-#193-AI报告刷新策略-无新事件跳过-实现方案.md §4.3
    # off      = 不判定, 行为与改造前完全一致(回滚位)
    # dry_run  = 判定但照常生成, "本可跳过"写 system_events 留痕(影子期验证, 默认)
    # enforce  = 判定为无新事件则跳过(不调 LLM), 写留痕
    AI_PREGEN_SKIP_MODE: str = "dry_run"
    # 距上次快照超过该分钟数 → 必生成(保窗口时刻不大幅落后, 见方案 §4.2)
    AI_PREGEN_MAX_STALE_MINUTES: int = 120
    # 灰度: 仅对这些模板 scope 生效(逗号分隔), 其余 scope 不判定照常生成
    AI_PREGEN_FRESHNESS_ENABLED_SCOPES: str = "refund,complaint,overview"

    # 跨域(局域网部署,允许内网访问)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # 初始管理员
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = "admin123"

    @model_validator(mode="after")
    def _enforce_prod_secrets(self) -> "Settings":
        """★ 统一启动期校验：占位符 + 模式↔库名一致性 + 生产弱口令。

        2026-09-22 追加（本机制的核心）：把三类**静默错配**变为启动期硬失败。
        原实现只校验了 ENV=prod 时的弱口令，**完全不校验库名** ——
        于是"prod 连到 dev 库"与"dev 连到生产库"都能静默运行到出事为止。
        """
        problems: list[str] = []

        # ── A. 库名占位符检查（所有模式）──
        #    忘记配置不能等价于"连真实库"，必须显式声明。
        if self.POSTGRES_DB == _PLACEHOLDER_DB:
            problems.append(
                f"POSTGRES_DB 未配置（当前为占位符 {_PLACEHOLDER_DB!r}）。\n"
                f"    必须在 .env 或环境变量中显式声明要连接的库名。\n"
                f"    ★ 为什么不再提供默认值：原默认值就是真实库名 `csctalon`，\n"
                f"      使「忘记配置」= 「连真实库」—— 2026-09-10 事故即由此而来。"
            )

        # ── B. 模式 ↔ 库名一致性（所有模式，见 guard.py）──
        #    这是让"连错库"从「靠运气」变为「启动即失败」的关键。
        if self.POSTGRES_DB != _PLACEHOLDER_DB:
            problems.extend(check_db_matches_profile(_PROFILE, self.database_url))

        # ── C. 生产模式弱口令检查（保留原有逻辑）──
        if self.ENV != "prod":
            if problems:
                raise ValueError(
                    "配置校验失败，拒绝启动：\n  - " + "\n  - ".join(problems)
                )
            return self

        def _has_placeholder(value: str) -> bool:
            low = value.lower()
            return any(k in low for k in ("please", "change_me", "changeme", "random", "example", "请修改", "<", ">"))

        if self.POSTGRES_PASSWORD == _PROD_FORBIDDEN_DEFAULTS["POSTGRES_PASSWORD"]:
            problems.append("POSTGRES_PASSWORD 仍为默认值 csctalon123")
        elif len(self.POSTGRES_PASSWORD) < 12:
            problems.append("POSTGRES_PASSWORD 过短(<12), 建议使用强密码")
        elif _has_placeholder(self.POSTGRES_PASSWORD):
            problems.append("POSTGRES_PASSWORD 疑似占位符(含 <请修改...> 等), 必须改为强随机口令")

        if self.JWT_SECRET_KEY == _PROD_FORBIDDEN_DEFAULTS["JWT_SECRET_KEY"]:
            problems.append("JWT_SECRET_KEY 仍为默认占位值, 可被伪造任意用户 token")
        elif len(self.JWT_SECRET_KEY) < 32:
            problems.append("JWT_SECRET_KEY 长度不足 32 字符, 必须使用强随机密钥")
        elif _has_placeholder(self.JWT_SECRET_KEY):
            problems.append(
                "JWT_SECRET_KEY 疑似占位短语(含 please/change/random/请修改 等词), 必须改为强随机密钥"
            )

        if self.INIT_ADMIN_PASSWORD == _PROD_FORBIDDEN_DEFAULTS["INIT_ADMIN_PASSWORD"]:
            problems.append("INIT_ADMIN_PASSWORD 仍为 admin123")
        elif len(self.INIT_ADMIN_PASSWORD) < 8:
            problems.append("INIT_ADMIN_PASSWORD 过短(<8)")
        elif _has_placeholder(self.INIT_ADMIN_PASSWORD):
            problems.append("INIT_ADMIN_PASSWORD 疑似占位符(含 <请修改...> 等), 必须改为强口令")

        if not self.REDIS_PASSWORD:
            problems.append("REDIS_PASSWORD 为空, Redis 无密码裸奔")
        elif len(self.REDIS_PASSWORD) < 8:
            problems.append("REDIS_PASSWORD 过短(<8)")
        elif _has_placeholder(self.REDIS_PASSWORD):
            problems.append("REDIS_PASSWORD 疑似占位符(含 <请修改...> 等), 必须改为强口令")

        if problems:
            raise ValueError(
                "生产环境(ENV=prod)安全配置校验失败, 拒绝启动:\n  - "
                + "\n  - ".join(problems)
                + "\n请在 .env 中设置强随机值后再启动。"
            )
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def redis_url(self) -> str:
        # 带 password 的 redis URL; 无密码时省略避免 redis://:None@...
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
