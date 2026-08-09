-- ============================================================
-- 占位手动建表 SQL: policies + dept_info_syncs
--
-- 若不使用 Alembic 迁移,可直接在 PostgreSQL 中执行本文件。
-- 若已使用 Alembic,请执行 `cd backend && alembic upgrade head` 即可
-- (版本号: a2c4e6f8b0d2_add_policies_and_dept_syncs)。
--
-- 注意:请先确认 users 表存在(users.id 为 FK 目标)。
-- ============================================================

-- 1. policies 管理制度/服务规范/流程SOP/话术/预案
CREATE TABLE IF NOT EXISTS policies (
    id                  BIGSERIAL PRIMARY KEY,
    title               VARCHAR(255)    NOT NULL,
    category            VARCHAR(32)     NOT NULL DEFAULT 'other',
    summary             VARCHAR(500),
    content             TEXT,
    attachment_urls     JSONB,
    version             VARCHAR(32),
    status              VARCHAR(32)     NOT NULL DEFAULT 'draft',
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    owner_id            INTEGER         REFERENCES users(id) ON DELETE SET NULL,
    approver_id         INTEGER         REFERENCES users(id) ON DELETE SET NULL,
    approved_at         TIMESTAMPTZ,
    approver_note       TEXT,
    view_count          INTEGER         NOT NULL DEFAULT 0,
    created_by          VARCHAR(64),
    remark              TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  policies IS '管理制度/服务规范/流程SOP/沟通话术/应急预案';
COMMENT ON COLUMN policies.category IS '分类:service_standard/process_sop/management_rule/communication_script/incident_response/other';
COMMENT ON COLUMN policies.status   IS '发布状态:draft/pending_approval/published/archived';
CREATE INDEX IF NOT EXISTS ix_policies_category ON policies (category);
CREATE INDEX IF NOT EXISTS ix_policies_status   ON policies (status);


-- 2. dept_info_syncs 部门间信息同步登记
CREATE TABLE IF NOT EXISTS dept_info_syncs (
    id                      BIGSERIAL PRIMARY KEY,
    title                   VARCHAR(255)    NOT NULL,
    category                VARCHAR(32)     NOT NULL DEFAULT 'other',
    priority                VARCHAR(16)     NOT NULL DEFAULT 'normal',
    status                  VARCHAR(24)     NOT NULL DEFAULT 'draft',
    from_department         VARCHAR(128),
    to_departments          VARCHAR(500),
    content                 TEXT,
    impact                  TEXT,
    attachment_urls         JSONB,
    related_incident_id     INTEGER,
    related_measure_id      INTEGER,
    effective_from          TIMESTAMPTZ,
    deadline                TIMESTAMPTZ,
    sender_id               INTEGER         REFERENCES users(id) ON DELETE SET NULL,
    receiver_ids            JSONB,
    read_receipts           JSONB,
    response                TEXT,
    responded_at            TIMESTAMPTZ,
    responded_by            VARCHAR(64),
    closed_at               TIMESTAMPTZ,
    closed_by               VARCHAR(64),
    created_by              VARCHAR(64),
    remark                  TEXT,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  dept_info_syncs IS '部门间信息同步登记(规则/流程/活动/风险/数据/协作)';
COMMENT ON COLUMN dept_info_syncs.category IS '分类:rule_change/process_change/activity_notice/risk_alert/data_sync/coordination/other';
COMMENT ON COLUMN dept_info_syncs.priority IS '优先级:low/normal/high/urgent';
COMMENT ON COLUMN dept_info_syncs.status   IS '状态:draft/sent/read/responded/closed';
CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_category ON dept_info_syncs (category);
CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_priority ON dept_info_syncs (priority);
CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_status   ON dept_info_syncs (status);


-- ============================================================
-- TODO 占位(真实接入时按需补):
--  - 若需要快速按 发起部门/接收部门 检索,可再加:
--      CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_from_dept ON dept_info_syncs (from_department);
--      CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_to_dept_gin ON dept_info_syncs USING gin (to_departments gin_trgm_ops);
--  - 若要对 JSONB 数组 receiver_ids 做成员检索,可加 GIN 索引:
--      CREATE INDEX IF NOT EXISTS ix_dept_info_syncs_receiver_ids_gin ON dept_info_syncs USING gin (receiver_ids);
-- ============================================================
