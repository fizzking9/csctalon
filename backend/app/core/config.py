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
    APP_NAME: str = "客服中心管理系统"
    APP_VERSION: str = "0.1.0"
    ENV: Literal["dev", "prod", "test"] = "dev"
    DEBUG: bool = True
    # SQLAlchemy SQL 语句打印(engine echo)。默认关闭——即使 DEBUG=true 也不全量打 SQL,
    # 避免 SQL 噪音淹没真正错误、撑大日志。需要排障时显式设 SQL_ECHO=true。
    SQL_ECHO: bool = False

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

    # SCRM 微信聚合平台(对接客服接待评估报表)
    # 说明:默认种子值仅用于首次启动灌入 SyncSourceConfig(加密),实际以 DB 配置为准
    SCRM_BASE_URL: str = "https://gateway.bilinl.com"  # 平台 OpenAPI 基础地址
    SCRM_CLIENT_ID: str = ""  # ClientId(对应 SyncSourceConfig.username=Fernet(clientId))
    SCRM_CLIENT_SECRET: str = ""  # ClientSecret(对应 SyncSourceConfig.password=Fernet(secret))
    # 白音 SCRM 后台 Playwright 网页登录凭证(手机号/密码) —— 仅 fix 脚本灌入用,运行时从 DB 读
    # ⚠️ 严禁硬编码进脚本/入库; 通过环境变量或 .env 注入
    SCRM_WEB_USERNAME: str = ""
    SCRM_WEB_PASSWORD: str = ""

    # 跨域(局域网部署,允许内网访问)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # 初始管理员
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = "admin123"

    @model_validator(mode="after")
    def _enforce_prod_secrets(self) -> "Settings":
        """生产环境 fail-fast: 拒绝使用弱默认密钥/口令。

        ENV=prod 时若仍用默认 POSTGRES_PASSWORD/JWT_SECRET_KEY/INIT_ADMIN_PASSWORD/REDIS_PASSWORD,
        立即抛错阻止启动——漏配 .env 即裸奔是真实事故场景。
        dev/test 环境放宽, 便于本地开箱即用。
        """
        if self.ENV != "prod":
            return self
        problems = []
        if self.POSTGRES_PASSWORD == _PROD_FORBIDDEN_DEFAULTS["POSTGRES_PASSWORD"]:
            problems.append("POSTGRES_PASSWORD 仍为默认值 csctalon123")
        if self.JWT_SECRET_KEY == _PROD_FORBIDDEN_DEFAULTS["JWT_SECRET_KEY"]:
            problems.append("JWT_SECRET_KEY 仍为默认占位值, 可被伪造任意用户 token")
        if self.INIT_ADMIN_PASSWORD == _PROD_FORBIDDEN_DEFAULTS["INIT_ADMIN_PASSWORD"]:
            problems.append("INIT_ADMIN_PASSWORD 仍为 admin123")
        if not self.REDIS_PASSWORD:
            problems.append("REDIS_PASSWORD 为空, Redis 无密码裸奔")
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
