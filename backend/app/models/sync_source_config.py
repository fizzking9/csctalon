"""BMS / SCRM 同步源运行配置模型 - URL/凭证/开关/抓取模式

与 data_sources(分析元数据)分离:这里只存"如何去抓"的运行参数。
账号密码用 Fernet 加密存储(见 app/core/crypto.py)。

新增 options(JSONB) 存储所有扩展参数,避免频繁改表:
  - fetch_mode:        "openapi"     → OpenAPI 方式(SCRM 默认,clientId/secret)
                       "playwright"  → 网页 Playwright 抓取(BMS 默认/闪猫)
                       "http"        → 纯 HTTP API 导出(cs_record)
  - captcha_type:      "none"        → 登录无验证码
                       "text"        → 普通字符验证码(闪猫默认)
                       "math"        → 数学计算式验证码(SCRM 网页登录,需计算后填结果)
  - login_url_path:    登录页路径(默认同 base_url+/login)
  - captcha_img_selector: 验证码图片 CSS 选择器(覆盖默认)
  - username_selector:    账号输入框选择器
  - password_selector:    密码输入框选择器
  - captcha_input_selector: 验证码输入框选择器
  - submit_selector:      登录按钮选择器
  - 以及未来任何抓取相关参数都可以放 options 里
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SyncSourceConfig(IDMixin, TimestampMixin, Base):
    """BMS/SCRM 同步源运行配置 - 每行一个可同步源"""

    __tablename__ = "sync_source_configs"

    data_type: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="refund/ticket/wallet/scrm_operator..."
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, comment="基础地址(域名+子路径前缀)")
    page_path: Mapped[str] = mapped_column(String(256), nullable=False, comment="页面路径/接口路径")
    group_key: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="所属数据源分组(source_groups.group_key), 组级凭证继承"
    )
    time_labels: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="时间筛选标签(页面上有多个时间框时按标签精确填,如 ticket)"
    )
    username: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="用户名:BMS=BMS账号; SCRM openapi=ClientId; SCRM网页=登录账号(Fernet加密)"
    )
    password: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="密码:BMS=BMS密码; SCRM openapi=ClientSecret; SCRM网页=登录密码(Fernet加密)"
    )
    options: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="扩展参数:抓取模式/验证码类型/各种选择器等"
    )
    default_sync: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否纳入定时同步/手动同步默认勾选"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="总开关")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
