"""用户管理路由"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.security import hash_password
from app.models.operation_log import OperationLog
from app.models.user import RoleEnum, User
from app.schemas.common import PageBase, R
from app.schemas.user import UserBrief, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["用户管理"])


@router.get("", response_model=R[PageBase[UserBrief]], summary="用户列表")
def list_users(
    db: DBSession,
    _: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    role: RoleEnum | None = None,
):
    stmt = select(User)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(User.username.like(kw) | User.display_name.like(kw))
    if role:
        stmt = stmt.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(data=PageBase(items=[UserBrief.model_validate(u) for u in items], total=total or 0, page=page, page_size=page_size))


@router.post("", response_model=R[UserOut], summary="创建用户")
def create_user(payload: UserCreate, db: DBSession, _: AdminUser):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="用户名已存在")
    # 2026-08-20 审计 SEC-13: bcrypt 仅支持 72 字节, 超长密码转 400 而非 500
    try:
        hashed = hash_password(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        hashed_password=hashed,
        role=payload.role.value,
        group_name=payload.group_name,
        qw_name=payload.qw_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return R(data=UserOut.model_validate(user))


@router.put("/{user_id}", response_model=R[UserOut], summary="更新用户")
def update_user(user_id: int, payload: UserUpdate, db: DBSession, current: CurrentUser):
    # 普通用户只能改自己,管理员可改任意
    if current.role != RoleEnum.ADMIN.value and current.id != user_id:
        raise HTTPException(status_code=403, detail="只能修改自己的信息")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = payload.model_dump(exclude_unset=True)
    password_changed = False
    if password := data.pop("password", None):
        # 验旧密码: 用户改自己密码必须验旧(防会话劫持后改密锁户);
        # 管理员重置他人密码(current.id != user_id)不强制验旧
        old_password = data.pop("old_password", None)
        require_old = current.id == user_id
        if require_old:
            if not old_password:
                raise HTTPException(status_code=400, detail="修改自己的密码需提供原密码(old_password)")
            from app.core.security import verify_password
            if not verify_password(old_password, user.hashed_password):
                raise HTTPException(status_code=400, detail="原密码错误")
        # 2026-08-20 审计 SEC-13: 超长密码(>72 字节)转 400
        try:
            user.hashed_password = hash_password(password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        password_changed = True
    else:
        # 即便不改密, old_password 字段也不应残留入库
        data.pop("old_password", None)
    # 非管理员不能改角色
    if current.role != RoleEnum.ADMIN.value:
        data.pop("role", None)
        data.pop("is_active", None)
    else:
        # role 是 RoleEnum,存为字符串
        if "role" in data and data["role"] is not None:
            data["role"] = payload.role.value if payload.role else data["role"]
    for k, v in data.items():
        setattr(user, k, v)
    # M7: 改密后自增 token_version, 使该用户所有已签发 token 立即失效(含当前会话, 需重新登录)
    if password_changed:
        user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(user)
    return R(data=UserOut.model_validate(user))


@router.delete("/{user_id}", response_model=R, summary="删除用户")
def delete_user(user_id: int, db: DBSession, current: AdminUser):
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 2026-08-30 修复: 删除用户前先清理外键关联引用。
    # 此前直接 db.delete(user) 在 user 被 strategy_read_receipts / wechat_accounts 等
    # 无 ON DELETE 动作的表引用时, 触发 ForeignKeyViolation → 未捕获 → 500。
    # 方案: ① NOT NULL 关联行显式删除(已读回执/微信绑定/通知/登录绑定记录)
    #       ② 可空关联列显式置 NULL(保留业务数据, 解绑用户引用)
    #       ③ 外层再捕获 IntegrityError 兜底(防止未来新增外键再打 500)。
    try:
        _purge_user_references(db, user.id)
        db.delete(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="该用户存在关联业务数据, 删除失败(请先解除其负责人/审批人等关联)")
    return R()


def _purge_user_references(db: Session, user_id: int) -> None:
    r"""删除用户前清理其全部外键引用, 避免删除时外键约束 500。

    覆盖范围与 users 表全部外键引用对齐(见数据库 \d users 的 Referenced by)。
    每一步都在独立 SAVEPOINT 中执行: 单个语句失败(如某环境列不存在)只回滚该步,
    不影响其余清理与后续用户删除。
    """
    from app.models.analysis_view import AnalysisView, ClassificationRule
    from app.models.audit_note import AuditNote
    from app.models.complaint_audit import ComplaintAudit
    from app.models.dashboard import Dashboard
    from app.models.dept_info_sync import DeptInfoSync
    from app.models.expense import ExpenseInvoice, Reimbursement
    from app.models.incident import Incident, IncidentFollowup
    from app.models.measure import Measure
    from app.models.metric import MetricThreshold
    from app.models.notification import Notification
    from app.models.person import Person
    from app.models.policy import Policy
    from app.models.report import Report
    from app.models.report_template import ReportTemplate
    from app.models.scrm_staff_profile import ScrmStaffProfile
    from app.models.strategy import StrategyReadReceipt
    from app.models.task import SpecialTask, TaskFollowup
    from app.models.todo import TodoFollowup, TodoItem
    from app.models.upstream_ticket import UpstreamTicket
    from app.models.wechat import WechatAccount, WechatRegistration
    from app.models.weekly_section_binding import WeeklySectionBinding

    # ① NOT NULL 关联 → 显式删除(业务上随用户一并清理)
    _sp(db, delete(StrategyReadReceipt).where(StrategyReadReceipt.user_id == user_id))  # 已读回执(注销阅读记录)
    _sp(db, delete(WechatAccount).where(WechatAccount.user_id == user_id))  # 微信账号绑定(解除全部微信身份)
    _sp(db, delete(Notification).where(Notification.recipient_id == user_id))  # 站内通知(CASCADE 语义, 显式删除更清晰)
    # 操作日志 user_id 置 NULL(保留审计痕迹)
    _sp(db, update(OperationLog).where(OperationLog.user_id == user_id).values(user_id=None))

    # ② 可空关联列 → 置 NULL(保留业务数据, 解除用户引用)
    _sp(db, update(AnalysisView).where(AnalysisView.owner_id == user_id).values(owner_id=None))
    _sp(db, update(ClassificationRule).where(ClassificationRule.owner_id == user_id).values(owner_id=None))
    _sp(db, update(AuditNote).where(AuditNote.author_id == user_id).values(author_id=None))
    _sp(db, update(ComplaintAudit).where(ComplaintAudit.audited_by == user_id).values(audited_by=None))
    _sp(db, update(Dashboard).where(Dashboard.owner_id == user_id).values(owner_id=None))
    _sp(db, update(DeptInfoSync).where(DeptInfoSync.sender_id == user_id).values(sender_id=None))
    _sp(db, update(ExpenseInvoice).where(ExpenseInvoice.submitter_id == user_id).values(submitter_id=None))
    _sp(db, update(Incident).where(Incident.registrar_id == user_id).values(registrar_id=None))
    _sp(db, update(IncidentFollowup).where(IncidentFollowup.author_id == user_id).values(author_id=None))
    _sp(db, update(Measure).where(Measure.owner_id == user_id).values(owner_id=None))
    _sp(db, update(MetricThreshold).where(MetricThreshold.updated_by == user_id).values(updated_by=None))
    _sp(db, update(Person).where(Person.user_id == user_id).values(user_id=None))
    _sp(db, update(Policy).where(Policy.owner_id == user_id).values(owner_id=None))
    _sp(db, update(Policy).where(Policy.approver_id == user_id).values(approver_id=None))
    _sp(db, update(Reimbursement).where(Reimbursement.submitter_id == user_id).values(submitter_id=None))
    _sp(db, update(Reimbursement).where(Reimbursement.approved_by == user_id).values(approved_by=None))
    _sp(db, update(Report).where(Report.assignee_id == user_id).values(assignee_id=None))
    _sp(db, update(ReportTemplate).where(ReportTemplate.owner_id == user_id).values(owner_id=None))
    _sp(db, update(ScrmStaffProfile).where(ScrmStaffProfile.user_id == user_id).values(user_id=None))
    _sp(db, update(SpecialTask).where(SpecialTask.owner_id == user_id).values(owner_id=None))
    _sp(db, update(TaskFollowup).where(TaskFollowup.author_id == user_id).values(author_id=None))
    _sp(db, update(TodoFollowup).where(TodoFollowup.author_id == user_id).values(author_id=None))
    _sp(db, update(TodoItem).where(TodoItem.assignee_id == user_id).values(assignee_id=None))
    _sp(db, update(UpstreamTicket).where(UpstreamTicket.owner_id == user_id).values(owner_id=None))
    _sp(db, update(WechatRegistration).where(WechatRegistration.reviewed_by == user_id).values(reviewed_by=None))
    _sp(db, update(WechatRegistration).where(WechatRegistration.user_id == user_id).values(user_id=None))
    _sp(db, update(WeeklySectionBinding).where(WeeklySectionBinding.owner_id == user_id).values(owner_id=None))


def _sp(db: Session, stmt) -> None:
    """在独立 SAVEPOINT 中执行一条清理语句, 失败仅回滚该语句(不影响整体删除)"""
    try:
        with db.begin_nested():
            db.execute(stmt)
    except Exception:
        # 个别环境列名/模型差异时跳过该步, 不影响主删除流程
        pass
