# Alembic Migration 管理规范

> 版本：v1.0 · 日期：2026-08-17
> 关联代码：[backend/alembic/](../backend/alembic/)
> 当前 head：`n1b2c3d4e5f6`（单一 head）

## 一、版本链现状

```
d9b7f1b89c1d (init)
  └── ... 31 个版本 ...
      └── z9y8x7w6v5u4 (add_scrm_models)
          ├── a1b2c3d4e5f7 → ... → aa11bb22cc33 (expand varchars)  [分支A]
          └── a2c4e6f8b0d2 (add policies + dept_syncs)              [分支B]
                                   ↓
              m0a1b2c3d4e5 (merge heads: A + B)                    [merge]
                                   ↓
              n1b2c3d4e5f6 (add groups + specialties tables)       [当前 head]
```

- 总版本数：34 个 migration
- 当前 head：**1 个**（`n1b2c3d4e5f6`）
- 曾有 2 个 head（`a2c4e6f8b0d2` + `aa11bb22cc33`），已通过 merge migration 合并

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
2. **不要修改已提交的 migration 文件**：已应用的环境不会重新执行
3. **downgrade 必须可实现**：以便回滚
4. **单 worker 模式**：docker-compose.yml 中 `gunicorn -w 1` 保证互斥锁有效
5. **alembic_version 表应只有 1 行**：正常状态为当前 head 版本号；多行表示异常（可用 `alembic stamp` 修复）
