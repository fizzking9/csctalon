"""投诉路由聚合: APIRouter(prefix=/complaints) 单一定义, 子模块经 @router.get/post 注册。"""

from fastapi import APIRouter

router = APIRouter(prefix="/complaints", tags=["投诉分析"])
