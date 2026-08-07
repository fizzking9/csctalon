"""v1 路由聚合"""

from fastapi import APIRouter

from app.api.v1 import analysis_views, analytics, auth, dashboards, data_sources, devices, fetch, incidents, match, measures, operation_logs, persons, report_comments, report_templates, reports, scrm_chat, scrm_staff, staff_assignments, strategy, system, tasks, todos, uploads, users, weekly_reports, wide_tables

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(uploads.router)
api_router.include_router(fetch.router)
api_router.include_router(match.router)
api_router.include_router(analytics.router)
api_router.include_router(devices.router)
api_router.include_router(todos.router)
api_router.include_router(tasks.router)
api_router.include_router(measures.router)
api_router.include_router(incidents.router)
api_router.include_router(reports.router)
api_router.include_router(weekly_reports.router)
api_router.include_router(report_templates.router)
api_router.include_router(report_comments.router)
api_router.include_router(strategy.router)
api_router.include_router(data_sources.router)
api_router.include_router(staff_assignments.router)
api_router.include_router(persons.router)
api_router.include_router(analysis_views.router)
api_router.include_router(analysis_views.rules_router)
api_router.include_router(dashboards.router)
api_router.include_router(operation_logs.router)
api_router.include_router(system.router)
api_router.include_router(wide_tables.router)
api_router.include_router(scrm_staff.router)
api_router.include_router(scrm_chat.router)
