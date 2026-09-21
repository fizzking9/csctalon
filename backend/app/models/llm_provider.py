"""LLM 多 Provider 模型 - 工单 #108

设计:
- 每行 = 一个 OpenAI 兼容服务接入(本地 Talon 网关 / 云端 DeepSeek 等)。
- API key 用 Fernet 加密入库(同 sync_source_configs 方式); 运行时解密使用。
- priority: 数字小=优先级高; 调用时按 priority 升序遍历, transient 错误降级下一个。
- enabled: 总开关; false=跳过不调用。
- healthy: 运行时健康状态; 连续失败 N 次自动置 false(降级), 健康探测成功后恢复。
- llm_call_logs: 每次调用一行审计(耗时/状态/错误), 不打敏感数据。
- 兼容旧 .env: 库为空时回退 settings.LLM_* 单源模式(向后兼容)。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class LlmProvider(IDMixin, TimestampMixin, Base):
    """LLM 服务接入 - 每行一个 Provider"""

    __tablename__ = "llm_providers"

    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="唯一标识(如 talon-gmk-qwen3-coder-30b)",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名(如 Talon·GMK·qwen3-coder:30b)")
    base_url: Mapped[str] = mapped_column(
        String(256), nullable=False,
        comment="OpenAI 兼容完整前缀(是否含 /v1 以服务商为准, llm_client 统一拼 /chat/completions)",
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名(如 qwen3-coder:30b)")
    api_key_cipher: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="API key(Fernet 加密); 无鉴权服务(本地无 key)可留空",
    )
    api_key_prefix: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="明文 key 前 6 位(管理界面识别用, 不脱敏可逆)"
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=100, nullable=False, index=True,
        comment="优先级(小=高); 调用按 priority 升序遍历, transient 错误降级",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True, comment="总开关; false=跳过不调用",
    )
    pregen_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
        comment=(
            "#131 仅供后台预生成: true=不进实时默认调用链(用户问答/时间解析), "
            "不参与启动 warmup; 仅当调用方显式按 provider_code 指定时置顶使用。"
            "用于 122b 等慢而重的预生成专用模型"
        ),
    )
    # ── 数据出域标记（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md）──
    # true = 该 provider 会把数据发往公网（如 api.deepseek.com）。
    # 受全局策略 settings.LLM_CLOUD_POLICY 管控，可被从调用链中整体移除。
    # 默认 false（保守：本地 provider 占多数，新增的云端需显式标记）。
    is_cloud: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False, index=True,
        comment="是否出域(公网/云端); true 时受 LLM_CLOUD_POLICY 管控",
    )
    # ── 角色归属（2026-09-21）──
    # 显式归属某个能力角色（assistant/specialist/expert）；NULL = 不限角色。
    # role_order: 角色内兜底顺序（小=先尝试）。
    role_scope: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True,
        comment="角色归属(assistant/specialist/expert); NULL=不限角色",
    )
    role_order: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False,
        comment="角色内兜底顺序(小=先尝试); 仅 role_scope 非空时有意义",
    )
    healthy: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True,
        comment="运行时健康状态; 连续失败自动置 false(降级), 健康探测成功后恢复",
    )
    # 调用层参数(可被 chat_completion 的 kwargs 覆盖)
    thinking: Mapped[str] = mapped_column(
        String(16), default="disabled", nullable=False,
        comment="思考模式: disabled/low/high; DeepSeek 专用, 其它服务忽略",
    )
    timeout_s: Mapped[float] = mapped_column(
        default=120.0, nullable=False, comment="生成 read 超时(秒)",
    )
    retry_max: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False, comment="瞬时失败(5xx/429/超时)指数退避重试次数",
    )
    # 健康探测(连续失败 N 次自动标 unhealthy; 半开探测间隔)
    fail_threshold: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False,
        comment="连续失败阈值; 达到后 healthy=false(降级)",
    )
    # 累计连续失败次数(运行时维护, 成功一次清零)
    fail_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="累计连续失败次数; 成功一次清零",
    )
    last_fail_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近失败时间(降级判定依据)"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近成功时间"
    )
    note: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="备注(用途/责任人/模型规格)"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后更新人")


class LlmCallLog(IDMixin, Base):
    """LLM 调用审计日志 - 每次调用一行(成功/失败均记)"""

    __tablename__ = "llm_call_logs"

    provider_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True,
        comment="Provider ID; NULL=回退 .env 单源模式(库为空时)",
    )
    provider_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="Provider code(快照, 防 Provider 删除后审计丢失)",
    )
    model: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="实际使用的模型名(快照)"
    )
    status: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False,
        comment="success/failed/transient/permanent/timeout",
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="HTTP 状态码")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="耗时(毫秒)")
    prompt_chars: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="入参 messages 拼接后字符数(不含敏感内容)"
    )
    out_chars: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="输出文本字符数"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="错误信息(脱敏: 不含 API key / 响应体正文)",
    )
    # 业务来源(便于按场景统计调用成功率)
    source: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True,
        comment="调用来源(如 ai_analyze / llm_time_parse / health_probe)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
