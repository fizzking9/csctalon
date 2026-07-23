"""v1 路由聚合"""

from fastapi import APIRouter

from app.api.v1 import analytics, auth, devices, match, todos, uploads, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(uploads.router)
api_router.include_router(match.router)
api_router.include_router(analytics.router)
api_router.include_router(devices.router)
api_router.include_router(todos.router)
