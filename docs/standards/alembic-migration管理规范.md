# Alembic Migration 管理规范

**性质**：元需求（META） —— 约束**所有**表结构变更


> 版本：v1.2 · 日期：2026-10-07（#211 增补：空库可重放 CI 门禁 +「修改已提交 migration」的例外，见 §二 / §八 第 6、7 条）
> 关联代码：[backend/alembic/](../backend/alembic/)
> 当前 head：以 `alembic heads` 实测为准 —— 2026-10-07 实测为 `t0u1v2w3x4y5`（单一 head）。
> 下方 §一 版本链图为 v1.1（2026-08-26）时点快照，版本号可能落后，**请勿据该图判断 head**。

## 一、版本链现状

```
d9b7f1b89c1d (init)
  └── ... 34 个版本 ...
      └── z9y8x7w6v5u4 (add_scrm_models)                           [branchpoint]
          ├── a1b2c3d4e5f7 → ... → aa11bb22cc33 (expand varchars)  [分支A]
          └── a2c4e6f8b0d2 (add policies + dept_syncs)             [分支B]
                                   ↓
              m0a1b2c3d4e5 (merge heads: A + B)                    [merge]
                                   ↓
              n1b2c3d4e5f6 → o1b2c3d4e5f6 → p1b2c3d4e5f6 → a0b1c2d3e4f5
              → b0c1d2e3f4a5 → c0d1e2f3a4b5 → w1x2y3z4a5b6 → r6f7a8b9c0d1
              → 64a584531400 → x2b3c4d5e6f7 → x3c4d5e6f7a8 → x4a5b6c7d8e9f
              → n9a8b7c6d5e4f → d5f6a7b8c9d0 → e1f2a3b4c5d6 → f0e1d2c3b4a5
              → f1e2d3c4b5a6 → f2e3d4c5b6a7 → g1a2b3c4d5e6 → h1i2j3k4l5m6
              → m7n8o9p0q1r2
                                   ↓
              73a7922ec598 (add_system_events_and_source_groups)  [当前 head]
```

- 总版本数：57 个 migration
- 当前 head：**1 个**（`73a7922ec598`，`alembic heads` 实测验证）
- 曾有 2 个 head（`a2c4e6f8b0d2` + `aa11bb22cc33`），已通过 merge migration `m0a1b2c3d4e5` 合并
- 关键里程碑：
  - `n1b2c3d4e5f6` + `o1b2c3d4e5f6`：Group/Specialty 双体系 + persons 冗余列
  - `p1b2c3d4e5f6`：users.token_version（JWT 吊销）
  - `a0b1c2d3e4f5`：Phase B（排班/发票/知识库/预警）
  - `b0c1d2e3f4a5`：Phase C（政策措施/投诉审计/跨部门协作/数据质量）
  - `c0d1e2f3a4b5`：微信注册/账号表 + users 微信字段
  - `y4d5e6f7a8b0`：merchant_refunds 主键 wx_refund_no → 自增 id
  - `y4d5e6f7a8b0` 之后新增 `refund_wide` 宽表、`shanmao_refunds`、`manual_refunds`、`ingest_mode_configs`、`doc_sync_configs`、指标预警、`system_events`/`source_groups` 等
- **注意**：若遇到"多个 head"提示，执行 `alembic heads` 排查后，用 merge 迁移合并。

## 二、常用命令

```bash
# 查看当前 head
alembic heads

# 查看数据库当前版本
alembic current

# 查看版本历史
alembic history

# 升级到最新
alembic upgrade head

# 升级到指定版本
alembic upgrade <revision>

# 降级一个版本
alembic downgrade -1

# 降级到指定版本
alembic downgrade <revision>

# 标记当前数据库到指定版本（不执行 DDL）
alembic stamp <revision>

# 自动生成 migration（需连接数据库）
alembic revision --autogenerate -m "description"

# ★空库可重放自检（CI 强制门禁，#211；规范依据见 §八 第 6、7 条）
#   在一个全新空库上从基线重放整条迁移链 —— create_all + stamp 覆盖不到的
#   「链断裂 / 多 head / DROP 了链上从未 CREATE 的对象」只有它能暴露。
createdb csctalon_migchk_dev            # 库名须 _dev 后缀，通过启动期模式守卫
APP_MODE=dev POSTGRES_DB=csctalon_migchk_dev python -m alembic upgrade head
python -m alembic heads                 # 必须只输出一行 (head)
dropdb csctalon_migchk_dev              # 校验后删除临时库
```

> **注意**：在 Docker 环境中，需在 backend 容器内执行：`docker exec csctalon-backend alembic <command>`

## 三、Migration 文件规范

### 3.1 文件命名

```
<revision>_<description>.py
# 示例: n1b2c3d4e5f6_add_groups_and_specialties_tables.py
```

### 3.2 文件结构

```python
"""<description>

<详细说明：为什么需要这个 migration，做了什么>

Revision ID: <revision>
Revises: <down_revision>
Create Date: YYYY-MM-DD HH:MM:SS.ffffff
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '<revision>'
down_revision: Union[str, None] = '<down_revision>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 升级逻辑
    pass


def downgrade() -> None:
    # 降级逻辑（必须可实现）
    pass
```

### 3.3 revision ID 生成

手动生成 12 位十六进制字符串，建议按字母顺序递增：
- `m0a1b2c3d4e5` → merge
- `n1b2c3d4e5f6` → 新功能
- 不要使用 `alembic revision` 自动生成的随机 ID（不易识别）

## 四、Merge Migration 规范

当出现多个 head 时，创建 merge migration 合并：

```python
revision: str = 'm0a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = ('head_a', 'head_b')  # 元组

def upgrade() -> None:
    pass  # merge 无需 DDL

def downgrade() -> None:
    pass  # merge 不支持 downgrade
```

## 五、幂等升级规范

对于已有表的环境（如通过 `Base.metadata.create_all()` 创建的表），migration 需使用幂等检查：

```python
def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t)"
    ), {"t": table_name})
    return bool(result.scalar())

def upgrade() -> None:
    if not _table_exists('my_table'):
        op.create_table('my_table', ...)
```

## 六、种子数据规范

migration 可包含种子数据插入，使用 `ON CONFLICT DO NOTHING` 保证幂等：

```python
conn = op.get_bind()
existing = conn.execute(sa.text("SELECT count(*) FROM my_table")).scalar()
if existing == 0:
    conn.execute(sa.text("""
        INSERT INTO my_table (code, name) VALUES
            ('a', 'A'), ('b', 'B')
        ON CONFLICT (name) DO NOTHING
    """))
```

## 七、操作流程

### 7.1 新环境部署

```bash
# 1. 启动数据库容器
docker compose up -d db

# 2. 执行 migration（自动建表 + 种子数据）
docker compose up -d backend  # backend 启动时自动 alembic upgrade head
```

### 7.2 已有环境更新

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 重建 backend 容器（如果 migration 文件有更新）
docker compose build --no-cache backend
docker compose up -d backend

# 3. 验证
docker exec csctalon-backend alembic current  # 应显示最新 head
```

### 7.3 表已存在但 migration 缺失（stamp）

当表已通过 `Base.metadata.create_all()` 创建，但 migration 文件新增时：

```bash
# 1. 确保 migration 文件使用幂等检查（_table_exists）
# 2. stamp 到最新版本（不执行 DDL，仅标记版本）
docker exec csctalon-backend alembic stamp <revision>
```

## 八、注意事项

1. **不要删除已提交的 migration 文件**：会导致版本链断裂
2. **原则上不要修改已提交的 migration 文件**：已应用的环境不会重新执行，改动对存量库无效、还会造成认知不一致。**唯一例外见第 7 条**（修复空库可重放性缺陷）。
3. **downgrade 必须可实现**：以便回滚
4. **单 worker 模式**：docker-compose.yml 中 `gunicorn -w 1` 保证互斥锁有效
5. **alembic_version 表应只有 1 行**：正常状态为当前 head 版本号；多行表示异常（可用 `alembic stamp` 修复）
6. **★迁移必须能在全新空库顺序 `upgrade head`（空库可重放，CI 强制门禁，#211）**：CI（`.gitea/workflows/test.yml`）在一个独立空库上执行 `alembic upgrade head` 并校验单一 head。测试库走 `create_all + stamp head`（见 [tests/conftest.py](../../backend/tests/conftest.py)），会**跳过全部迁移**——因此凡是"假设某约束/索引/列已被 create_all 带出"就直接 `DROP`/`ALTER` 的迁移，在存量库正常、在全新环境却会中断（典型报错 `UndefinedObject: constraint "xxx" does not exist`）。编写要求：对"可能不存在"的对象做 `DROP`/`ALTER` 前，先用 `pg_constraint` / `information_schema` 做存在性判断（或用 `IF EXISTS`），保证「全新空库」与「存量库」两条路径最终 schema 一致；提交前必须按 §二 的空库自检命令本地实测一遍。
7. **第 2 条的唯一例外——为修复空库可重放性缺陷可改已提交迁移**，但须同时满足：(a) **不改变**已应用环境的最终 schema 与数据（只加存在性判断/分支，让两条路径殊途同归）；(b) 不增删版本、不改 `revision` / `down_revision`；(c) 本地全新空库 `upgrade head` 实测通过。已应用该迁移的库不会重跑，故对生产无影响。首例：`a1c2e3f4d5b6`（#211）——其无条件 `DROP CONSTRAINT uq_cs_record_business` 在空库断裂（该约束链上从未 CREATE，历史上仅由 create_all 依模型带出），已改为先查 `pg_constraint`、存在才 DROP。
