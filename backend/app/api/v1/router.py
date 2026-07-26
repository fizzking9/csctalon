"""v1 路由聚合"""

from fastapi import APIRouter

from app.api.v1 import analysis_views, analytics, auth, data_sources, devices, match, operation_logs, persons, strategy, tasks, todos, uploads, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(uploads.router)
api_router.include_router(match.router)
api_router.include_router(analytics.router)
api_router.include_router(devices.router)
api_router.include_router(todos.router)
api_router.include_router(tasks.router)
api_router.include_router(strategy.router)
api_router.include_router(data_sources.router)
api_router.include_router(persons.router)
api_router.include_router(analysis_views.router)
api_router.include_router(analysis_views.rules_router)
api_router.include_router(operation_logs.router)
