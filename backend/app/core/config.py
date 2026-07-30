"""应用配置 - 基于环境变量,支持 .env 文件"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # JWT
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_PLEASE_USE_A_LONG_RANDOM_STRING"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1天

    # 敏感配置加密 key(用于 sync_source_configs 凭证加密);留空则从 JWT_SECRET_KEY 派生
    CONFIG_ENCRYPTION_KEY: str = ""

    # 上传
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 200

    # BMS 平台(定时同步用,手动同步时前端传入)
    BMS_USERNAME: str = ""
    BMS_PASSWORD: str = ""
    BMS_SCHEDULE_AUTOSTART: bool = False  # 启动时是否自动开启定时同步

    # 跨域(局域网部署,允许内网访问)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # 初始管理员
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = "admin123"

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
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
