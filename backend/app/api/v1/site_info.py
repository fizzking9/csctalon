"""站点公开信息 API - 网站备案信息(无需登录即可访问)

用于登录页与全局页脚展示 ICP 备案号 / 公安备案号 / 版权信息等。
信息来自环境变量(.env), 见 backend/app/core/config.py 中 ICP_BEIAN_NO 等字段。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.schemas.common import R


class SiteInfoOut(BaseModel):
    site_name: str
    site_subtitle: str
    icp_no: str
    psb_no: str
    beian_note: str
    copyright_holder: str


router = APIRouter(prefix="/public", tags=["站点信息"])


@router.get("/site-info", response_model=R[SiteInfoOut], summary="站点备案信息(公开)")
def get_site_info():
    """返回网站名称、ICP/公安备案号、备案备注与版权主体(公开,无需登录)"""
    return R(
        data=SiteInfoOut(
            site_name=settings.APP_NAME,
            site_subtitle=settings.SITE_SUBTITLE,
            icp_no=settings.ICP_BEIAN_NO,
            psb_no=settings.PSB_BEIAN_NO,
            beian_note=settings.BEIAN_NOTE,
            copyright_holder=settings.COPYRIGHT_HOLDER,
        )
    )
