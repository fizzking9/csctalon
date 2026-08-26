# CSCTalon — 客服中心数据分析决策与工作流系统

> **定位**：数据分析决策系统 + 日常工作流办公支撑系统
>
> 面向客服中心管理层的运营数据采集→清洗→分析→决策→闭环管理一体化平台。

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         浏览器 (Vue 3 SPA)                           │
│  运营看板 │ 工作台 │ 数据中心 │ 分析视图 │ 决策看板 │ 系统管理       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP (Nginx 反代)
┌──────────────────────────┴──────────────────────────────────────────┐
│                    后端 API (FastAPI + Gunicorn)                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 数据采集  │ │ 分析引擎  │ │ 看板服务  │ │ 工作流   │ │ 系统管理  │ │
│  │ BMS/SCRM │ │analytics │ │dashboard │ │task/todo │ │ persons  │ │
│  └─────┬────┘ └─────┬────┘ └──────────┘ └──────────┘ └──────────┘ │
│        │            │                                                 │
│  ┌─────┴────┐ ┌─────┴────────────────────────────────────────────┐  │
│  │ Playwright│ │ SQLAlchemy ORM (86张表) + Alembic (57个migration) │  │
│  │ OCR登录   │ └─────┬────────────────────────────────────────────┘  │
│  └──────────┘       │                                                │
└─────────────────────┼────────────────────────────────────────────────┘
                      │
              ┌───────┴───────┐
              │ PostgreSQL 15  │     Redis 7 (缓存+任务队列)
              │   (86张表)     │
              └───────────────┘
```

## 技术栈

| 层级 | 技术 | 说明 |
|---|---|---|
| 前端 | Vue 3 + Vite 5 + Element Plus | SPA, 75+ 页面组件 |
| 后端 | FastAPI + SQLAlchemy 2.0 + Gunicorn | 单 worker 保证互斥锁有效 |
| 数据库 | PostgreSQL 15 | 86 张表, SCD Type 2 历史表 |
| 缓存 | Redis 7 (AOF 持久化) | 任务队列 + 缓存 |
| 数据采集 | Playwright + ddddocr | 浏览器自动化 + OCR 自动登录 |
| 迁移 | Alembic | 57 个版本, 单一 head |
| 部署 | Docker Compose | 4 个服务: db/redis/backend/web |

## 快速启动

### 前置要求

- Docker + Docker Compose
- Git

### 步骤

```bash
# 1. 克隆仓库
git clone <repo-url> csctalon && cd csctalon

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 设置: POSTGRES_PASSWORD / JWT_SECRET / BMS账号密码 / SCRM凭证

# 3. 构建并启动 (首次约3-5分钟)
docker compose up -d --build

# 4. 验证
docker compose ps          # 4个服务均为 healthy/running
curl localhost:8000/health # 后端健康检查
# 浏览器打开 http://<服务器IP>  (默认账号: admin / admin123)
```

### 本地开发

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端
cd web
npm install
npm run dev   # http://localhost:5173
```

## 目录结构

```
csctalon/
├── backend/                       # FastAPI 后端
│   ├── app/
│   │   ├── models/                # 86 张数据表定义
│   │   ├── services/              # 业务逻辑 (analytics.py 是核心分析引擎)
│   │   ├── api/v1/                # 50+ API 路由 (~375 端点)
│   │   ├── core/                  # 配置/数据库/安全
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   └── utils/                 # 工具函数
│   ├── alembic/                   # 数据库迁移
│   │   └── versions/              # 57 个 migration (单一 head)
│   └── scripts/                   # 数据导入/维护脚本
├── web/                           # Vue 3 前端
│   └── src/
│       ├── views/                 # 页面组件 (75 个 .vue)
│       │   ├── dashboards/        # 运营看板 + 决策看板(10+子菜单)
│       │   ├── data/              # 数据采集统一入口(SyncDashboard)
│       │   ├── management/        # 工作台 + 管理闭环(报告/周报/制度/措施)
│       │   ├── analytics/         # 分析视图构建器
│       │   ├── system/            # 系统管理(人员/数据源/同步设置)
│       │   ├── audit/             # 审计
│       │   ├── strategy/          # 策略管理
│       │   ├── tasks/             # 任务管理
│       │   └── todos/             # 待办事项
│       ├── router/                # 路由配置
│       ├── api/                   # API 调用封装
│       ├── components/            # 公共组件
│       └── store/                 # Pinia 状态管理
├── docs/                          # 设计文档与说明
│   ├── 规划设计.md                 # 系统架构设计(v0.4)
│   ├── 数据同步与加工说明.md        # BMS/SCRM 同步链路(v0.4)
│   ├── 数据时间字段使用规范.md      # 时间字段口径
│   ├── 人均效率算法说明.md          # 工作量分/效率分计算
│   ├── Group-Specialty双体系设计.md # 行政分组+业务专项
│   ├── API接口文档.md              # 50+ 路由端点
│   ├── 数据库表结构说明.md          # 86 张表
│   ├── alembic-migration管理规范.md # 迁移管理
│   └── debug/                     # 调试文档归档
├── data/                          # Docker 数据卷 (gitignore)
├── logs/                          # 运行时日志 (gitignore)
├── .env.example                   # 环境变量模板
├── docker-compose.yml             # 4 个服务编排
└── README.md                      # 本文件
```

## 核心功能模块

### 1. 运营看板（首页）
- 日/周/月/季/年多粒度切换，快速日期选择 + 自定义范围
- 人均效率（工作量分 + 效率分，6 专项难度系数加权）
- 未闭环投诉列表（三表合一：BMS 工单 + 12315 外部 + 卡商投诉）
- 退款挽回摘要

### 2. 工作台
- 管理层视图：待办/任务/报告/制度/措施/事件闭环
- 客服视图：个人数据/待处理工单/快捷入口

### 3. 数据中心（统一采集入口）
- **SyncDashboard**：BMS + SCRM 合并同步表单 + 右侧实时日志面板
- BMS 同步：Playwright 浏览器自动化 + OCR 自动登录
- SCRM 同步：企微客服会话/转接/操作员增量同步
- 数据浏览/文件上传/采集记录/同步设置/数据源管理（Tab 切换）

### 4. 决策看板（管理层专属）
- 退款看板 / 投诉看板 / 接待看板 / 退款-提现全链路 / 绩效看板 / 专项跟进

### 5. 分析视图
- 可配置视图（SQL 模板 + 可视化图表）
- 10+ SCRM 预设视图（趋势/工作量/质量/响应/转接等）

### 6. 系统管理
- 人员管理：行政分组(Group) + 业务专项(Specialty) 双体系，SCD Type 2 历史追溯
- 预警中心：指标阈值化监测(优秀/达标/预警/告警四级水位)、预警规则、通知通道(站内信+群机器人/邮件可配置)
- 数据源管理 / 分类规则 / 操作日志 / 同步设置

## 文档索引

| 文档 | 说明 |
|---|---|
| [规划设计.md](docs/规划设计.md) | 系统架构、模块设计、技术路线 |
| [运营指标阈值化与预警通知设计.md](.trae/documents/metric-alerting-notification-design.md) | 指标注册表/四级阈值/预警引擎/通知通道设计与实施状态 |
| [数据同步与加工说明.md](docs/数据同步与加工说明.md) | BMS/SCRM 数据采集与清洗链路 |
| [人均效率算法说明.md](docs/人均效率算法说明.md) | 工作量分/效率分计算、6专项系数、SN去重 |
| [Group-Specialty双体系设计.md](docs/Group-Specialty双体系设计.md) | 行政分组与业务专项双维度模型 |
| [数据时间字段使用规范.md](docs/数据时间字段使用规范.md) | 各表时间字段口径定义 |
| [API接口文档.md](docs/API接口文档.md) | 50+ 路由端点/参数/响应 |
| [数据库表结构说明.md](docs/数据库表结构说明.md) | 86 张表用途/关系/索引 |
| [alembic-migration管理规范.md](docs/alembic-migration管理规范.md) | 版本链管理、merge head、幂等升级 |

## 环境变量

参考 `.env.example`，关键配置：

| 变量 | 说明 | 默认值 |
|---|---|---|
| POSTGRES_PASSWORD | 数据库密码 | （必填） |
| JWT_SECRET | JWT 签名密钥 | （必填） |
| BMS_SCHEDULE_AUTOSTART | 调度器自动启动 | True |
| BMS_SCHEDULE_HOURS | 定时同步小时 | 0,8,12,16,20 |

## 许可

内部使用，未授权。
