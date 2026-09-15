"""应用配置 - 基于环境变量,支持 .env 文件"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 生产环境禁止使用的弱默认值(fail-fast: 漏配 .env 即裸奔)
_PROD_FORBIDDEN_DEFAULTS = {
    "POSTGRES_PASSWORD": "csctalon123",
    "JWT_SECRET_KEY": "CHANGE_ME_IN_PRODUCTION_PLEASE_USE_A_LONG_RANDOM_STRING",
    "INIT_ADMIN_PASSWORD": "admin123",
    "REDIS_PASSWORD": "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "数据分析工具应用"
    APP_VERSION: str = "0.1.0"
    ENV: Literal["dev", "prod", "test"] = "dev"
    DEBUG: bool = True
    # SQLAlchemy SQL 语句打印(engine echo)。默认关闭——即使 DEBUG=true 也不全量打 SQL,
    # 避免 SQL 噪音淹没真正错误、撑大日志。需要排障时显式设 SQL_ECHO=true。
    SQL_ECHO: bool = False

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
    POSTGRES_PASSWORD: str = "csctalon123"
    POSTGRES_DB: str = "csctalon"
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

    # 上传
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 200
    # Playwright 登录态保存目录(独立于 UPLOAD_DIR, 不被 /uploads 静态挂载暴露)
    BROWSER_STATE_DIR: str = "./data/browser_states"

    # BMS 平台(定时同步用,手动同步时前端传入)
    BMS_USERNAME: str = ""
    BMS_PASSWORD: str = ""
    BMS_SCHEDULE_AUTOSTART: bool = True  # 启动时是否自动开启定时同步(默认开启,否则跨日/重启后整小时窗口都漏)
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
    # 喂给模型的数据底座(feed)长度上限(字符), 防 token 超限与延迟。
    # by_specialty 已精简(2026-09-04), 正常 feed <3KB; 此值作兜底防超长。
    # #98 (2026-09-15): 12000→6000。生产实测 llm_ms/total_ms=85~95%(#93 埋点),
    #   输入 token 直接推高首 token 延迟; 层2 上线后按 scope 切段 feed 更小。
    AI_ANALYZE_MAX_CHARS: int = 6000
    # AI 任务结果在内存保留时间(秒)与历史条数(单 worker 内存态)。
    AI_TASK_TTL_S: int = 600
    AI_TASK_HISTORY: int = 20
    # AI 单次回答最大输出 token(防超长生成拖慢响应); analyze 首查同步等待上限(秒)。
    # #98 (2026-09-15): 1500→800。生产实测 out_chars 多在 1200-2000(对应 800 token),
    #   且 out_chars 与 llm_ms 强正相关(2308字符→6484ms; 171字符→1479ms),
    #   降输出 token 上限直接砍 llm_ms 大头。
    AI_MAX_TOKENS: int = 800
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

    # 跨域(局域网部署,允许内网访问)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # 初始管理员
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = "admin123"

    @model_validator(mode="after")
    def _enforce_prod_secrets(self) -> "Settings":
        """生产环境 fail-fast: 拒绝弱默认密钥/口令与占位符。

        2026-08-20 审计 H-4/OPS-04 升级:
        - 原实现只精确比对 4 个字面默认值, .env.example 的占位符(如 <请修改为随机长字符串>)
          非空且不等于默认串即可绕过 → 弱密钥静默上线。
        - 现增加占位词/长度校验(尤其 JWT_SECRET_KEY, 它同时派生 Fernet 凭证加密密钥)。
        注意: 轮换 JWT_SECRET_KEY 前必须先设置独立 CONFIG_ENCRYPTION_KEY 并重加密
        sync_source_configs 存量凭证(见 .env.example 注释与审计报告 C-P0-4)。
        """
        if self.ENV != "prod":
            return self
        problems = []

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
