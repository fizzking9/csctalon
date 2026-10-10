# 客服中心管理系统 · 阿里云部署运维手册

> 版本: v1.4 · 2026-09-01
> 部署方式: 支持 **git 直连一键部署**（推荐）与 **tar 包发布** 两种，单机 Docker Compose，够用且易回滚。
> 目录: `deploy/` 下的脚本 + 本手册，覆盖 **安装 / 部署 / 升级 / 备份 / 数据导入导出 / 回滚 / 数字证书(HTTPS)** 全流程。
>
> 📌 **本手册 = 整体上云（数据在云端）**。若你需要 **数据留内网、计算在本地、外网可访问**，
> 请改用 [deploy/frp/README.md](frp/README.md) 的 **frp 内网穿透方案**（本地为主 + 云端转发），
> 两者可互补：frp 为主部署、云上作为异地备份仓库。

---

## 0. 高频运维指令速查 ⚡

> 服务器 = 部署机 `/opt/csctalon`。`current` 是软链 → `releases/<版本>`；**数据库/Redis 数据固定挂载在 `/opt/csctalon/data`（不随代码切换）**，代码目录随便切版本不影响数据。

**① 看状态 & 健康**
```bash
docker compose --env-file /opt/csctalon/.env ps     # 4 个容器: db/redis/backend/web
curl -sf http://127.0.0.1:8000/health && echo OK    # 后端健康
```

**② 升级发布（拉最新 main 并重建）**
```bash
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/upgrade.sh | sudo bash -s -- --git
# 升级前会自动备份; 失败自动回滚
```

**③ 备份（升级 / 大改动前必做）**
```bash
sudo /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14
```

**④ 恢复数据（误删 / 升级后"数据变空/异常"）**
```bash
sudo /opt/csctalon/current/deploy/restore.sh /opt/csctalon/backups/csctalon-backup-最近的.tar.gz --env-file /opt/csctalon/.env
```

**⑤ 回滚代码（功能异常回到上一版本）**
```bash
sudo /opt/csctalon/current/deploy/rollback.sh
# 数据也要退回升级前: sudo /opt/csctalon/current/deploy/rollback.sh --restore /opt/csctalon/backups/<升级前备份包>
```

**⑥ 查日志**
```bash
tail -n 200 -f /opt/csctalon/logs/backend/app.log    # 后端: 同步/调度/报错
docker logs --tail 200 csctalon-backend              # 容器内附加输出
docker logs --tail 200 csctalon-web                  # nginx 访问/错误
```

**⑦ 重置 admin 密码（登录一直报"用户名或密码错误"时）**
```bash
docker exec -i -e NEW_ADMIN_PASSWORD='新强密码' csctalon-backend python3 < py 脚本
```
inline 脚本（见下方一段；用户名若非 `admin` 则替换）：

```python
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User
import os
db = SessionLocal()
u = db.scalar(select(User).where(User.username == 'admin'))
if u is None:
    from app.models.user import RoleEnum
    u = User(username='admin', display_name='管理员', hashed_password=hash_password(os.environ['NEW_ADMIN_PASSWORD']), role=RoleEnum.ADMIN.value); db.add(u)
u.hashed_password = hash_password(os.environ['NEW_ADMIN_PASSWORD'])
db.commit(); db.close(); print('admin 密码已重置')
```

**⑧ 升级/迁移后"数据全空"急救三连（先判库，别急着删）**
```bash
# 1) 核心业务表到底有没有数据(0 或缺表 = 接错库/新空库)
docker exec csctalon-db psql -U csctalon -d csctalon -tAc "select 'refunds='||count(*) from refunds; select 'tickets='||count(*) from service_tickets;"
# 2) 旧数据中心目录还在不在
du -sh /opt/csctalon/data
# 3) 直接用它恢复(升级会自动先备到 backups/)
sudo /opt/csctalon/current/deploy/restore.sh /opt/csctalon/backups/csctalon-backup-最近的.tar.gz --env-file /opt/csctalon/.env
```

> **注意**
> - `INIT_ADMIN_PASSWORD` **只在首次创建管理员时生效**，之后改 `.env` 不会同步改 DB 密码 → 忘密码用 ⑦ 重置，别指望改 `.env`。
> - `current` 软链若异常（曾出现自引用死循环），用 `ls -l /opt/csctalon/current /opt/csctalon/current.old` 检查，并手工修正：
>   `ln -sfn /opt/csctalon/releases/<版本> /opt/csctalon/current`。
> - 千万别把数据目录建进 `releases/<版本>/data`（用相对 `./data/postgres` 就会"每切版本换个空库"，正是 "数据全空" 的常见根因）。

---

## 1. 体系总览

```
本地开发机                         阿里云服务器 (/opt/csctalon)
─────────────                      ─────────────────────────────
git push  ────────────────┐
deploy/package.sh 打包  ──┼──▶  方式A: bootstrap.sh / upgrade.sh --git  直接 git clone 拉代码
deploy/upload.sh  上传  ──┼──▶  方式B: packages/ ← 发布包 tar.gz
deploy/backup.sh  导出   ──┘      releases/<版本>/ ← 每版代码
                                 current → releases/<版本>  (软链)
                                 .env              生产环境变量(密钥/目录)
                                 data/             PG+Redis 数据(挂载, 不随代码动)
                                 uploads/          后端附件
                                 browser_states/   BMS/SCRM 登录态
                                 logs/             nginx/backend 日志
                                 backups/          backup.sh 产物(保留N份)
```

设计要点：

- **代码随版本切换、数据固定**：挂载路径通过 `.env` 指向 `/opt/csctalon/data` 等固定目录（见 [docker-compose.yml](../docker-compose.yml) 的 `${DB_DATA_DIR:-...}` 变量），所以 `current` 软链随便切版本，数据零影响。
- **升级即切版本**：新代码落到独立目录 → 切软链 → `docker compose up -d --build` 重建。后端启动自动执行 `alembic upgrade head`（幂等），**数据库结构无需手动迁移**。
- **备份/导入是独立脚本**：`backup.sh`（数据导出）、`restore.sh`（数据导入）单独使用，与安装/升级解耦，可随时手动跑或配置 cron。
- **免重复同步**：本地库已是全量数据，用 `backup.sh` 导出 → 服务器 `restore.sh` 导入，同步配置（含加密凭证）随库一起迁移。
- **开发环境取生产数据**：本地开发缺数据时，用 `sync-from-prod.sh`（本地执行）在生产机跑 `backup.sh` 生成快照 → 拉回本地 `pg_restore`。全程**只读生产**（生产 DB/Redis 均不写入），破坏性操作只作用本地。详见该脚本头部注释。

### 脚本清单

| 脚本 | 在哪儿执行 | 作用 |
|---|---|---|
| `bootstrap.sh` | 服务器（curl 一条命令） | **git 方式一键安装**：从 gitea 拉代码 → 调 install-server.sh 完成部署 |
| `install-server.sh` | 服务器 | 首次部署：装 Docker → 建目录 → 落盘代码 → 准备 `.env` → 构建启动 → 可选恢复数据 |
| `upgrade.sh` | 服务器 | 升级：自动备份 → 拉新代码 → 重建 → 健康检查（失败自动回滚）；支持 tar 包或 `--git` 两种来源 |
| `rollback.sh` | 服务器 | 回滚：代码切回历史版本 + 可选恢复备份数据 |
| `backup.sh` | 本地或服务器 | **数据导出**：数据库 + 附件 + 登录态（本地=导出迁移包；服务器=日常/升级前备份） |
| `restore.sh` | 服务器 | **数据导入**：从备份包恢复数据库 + 附件，支持 `--db-only` |
| `sync-from-prod.sh` | 本地开发机 | **开发取数**：生产机 `backup.sh` 快照 → 拉回本地 `pg_restore`（只读生产；Redis 默认不动，见其注释） |
| `package.sh` | 本地 | 打发布包 `dist/csctalon-<版本>-<时间戳>.tar.gz`（仅 tar 包方式需要） |
| `upload.sh` | 本地 | 发布包 scp 上传到服务器 `packages/`（仅 tar 包方式需要） |

---

## 2. 两种部署方式对比

| | 方式 A：git 直连（推荐） | 方式 B：tar 包发布 |
|---|---|---|
| 前提 | 服务器能访问 gitea `http://10.147.17.41:3000`（SD-WAN 可达） | 无网络限制要求 |
| 首次安装 | `curl .../bootstrap.sh \| sudo bash` 一条命令 | 本地打包 → 上传 → `install-server.sh` |
| 日常升级 | `curl .../upgrade.sh \| sudo bash -s -- --git` | 本地打包 → 上传 → `upgrade.sh <包>` |
| 优点 | 不打包不传包，本地 `git push` 后服务器直接拉 | 服务器不依赖 gitea 可达性；升级带自动回滚 |
| 注意 | 无自动回滚，升级前建议手动 `backup.sh` | 每次发版都要走"打包→上传"两步 |

> 方式 B 的 `upgrade.sh` 升级前自动备份，失败自动回滚，更适合生产严谨场景；方式 A 同样可以手动 `backup.sh` 补齐备份保障。

---

## 3. 首次部署（安装）

### 3.1 前置

- 一台阿里云 ECS（建议 2C4G 起），系统 Ubuntu 22.04/Debian 12。
- 安全组放行：`80`（HTTP）；`22`（SSH，仅办公 IP）；**不要**放行 `5432/6379/8000`（compose 已只绑 127.0.0.1）。
- **方式 A**：服务器能访问 gitea；**方式 B**：本地与服务器 SSH 免密可达。

### 3.2 方式 A：git 直连一键安装（推荐）

服务器上一条命令完成"下载代码 → 安装 → 可选导入数据"（未装 docker 会自动装）：

```bash
# 普通安装
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | sudo bash

# 首次部署含数据导入(推荐; --env-file/--restore 经 bash -s 透传)
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | \
  sudo bash -s -- --env-file /root/csctalon-prod.env --restore /opt/csctalon/packages/csctalon-backup-*.tar.gz
```

bootstrap.sh 支持参数（透传给 install-server.sh）：

| 参数 | 作用 |
|---|---|
| `--env-file <路径>` | 现成 `.env` 绝对路径（推荐，见 §3.4） |
| `--restore <备份包>` | 数据备份包绝对路径（首次迁移用 backup.sh 产物） |
| `--skip-install-docker` | 已装 docker 时跳过安装 |
| `--repo` / `--branch` | 指定仓库地址 / 分支（默认 gitea main） |

### 3.3 方式 B：tar 包发布

```bash
# 本地开发机
cd /path/to/csctalon
./deploy/package.sh                # 打发布包(自动取 git 版本号, 可传自定义: ./deploy/package.sh v1.0.0)
./deploy/backup.sh                 # 导出本地全量数据 → ./backups/csctalon-backup-*.tar.gz
./deploy/upload.sh root@<服务器IP>  # 上传发布包 → 服务器 /opt/csctalon/packages/
scp ./backups/csctalon-backup-*.tar.gz root@<服务器IP>:/opt/csctalon/packages/

# 服务器上
cd /opt/csctalon/packages && tar xzf csctalon-*.tar.gz -C /tmp/csctalon_pkg
sudo bash /tmp/csctalon_pkg/deploy/install-server.sh \
  --pkg /opt/csctalon/packages/csctalon-*.tar.gz \
  --env-file /opt/csctalon/.env \
  --restore /opt/csctalon/packages/csctalon-backup-*.tar.gz
```

> `--restore` 会在服务启动后调用 restore.sh 导入数据库与附件。完成后直接 `http://<服务器IP>/` 登录，**数据已在、同步配置已在，无需再跑同步**。

### 3.4 准备 .env（关键，两种方式通用）

`.env` 是唯一无法全自动的部分，必须人工准备：

- `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `INIT_ADMIN_PASSWORD` / `WORKBUDDY_SYNC_TOKEN`：改强密码。
- **`JWT_SECRET_KEY` / `CONFIG_ENCRYPTION_KEY`：直接沿用本地 `backend/.env` 的值**（否则同步源加密凭证解不开，就真的要重新同步/重录了）。
- `CORS_ORIGINS=http://<公网IP或域名>`。
- 六个目录变量（`DB_DATA_DIR` 等）保持模板默认（/opt/csctalon/...），install 脚本会创建。

模板：`deploy/env.production.example`。方式 B 可直接参考模板建 `.env` 后 `--env-file` 传入；方式 A 同理。

### 3.5 验证

```bash
docker compose --env-file /opt/csctalon/.env ps   # 四个容器 healthy
curl http://127.0.0.1:8000/health                 # 200
```

浏览器登录 → 看今日/运营/决策看板数据是否为本地那份 → 数据采集页跑一次增量确认同步源可用。

---

## 4. 日常升级

### 4.1 方式 A：git 直连升级

本地 `git push` 后，服务器一条命令拉最新代码并重建：

```bash
# 拉取 gitea main 分支最新代码 → 重建 → 健康检查
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/upgrade.sh | \
  sudo bash -s -- --git

# 指定分支
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/upgrade.sh | \
  sudo bash -s -- --git --branch dev
```

> 注意：upgrade.sh 的 `.env` 路径写死为 `/opt/csctalon/.env`，**不需要也不接受** `--env-file` / `--skip-install-docker`（那是 bootstrap.sh / install-server.sh 的参数，传了会被当成发布包路径而报错）。

### 4.2 方式 B：tar 包升级

```bash
# 本地
git tag vX.Y.Z && git push && git push --tags
./deploy/package.sh vX.Y.Z
./deploy/upload.sh root@<IP> dist/csctalon-vX.Y.Z-*.tar.gz

# 服务器
sudo bash /opt/csctalon/packages/csctalon-vX.Y.Z-*/deploy/upgrade.sh /opt/csctalon/packages/csctalon-vX.Y.Z-*.tar.gz
```

### 4.3 upgrade.sh 参数表

| 参数 | 作用 |
|---|---|
| `<发布包路径>` | tar 包方式：升级到该发布包版本 |
| `--git` | git 方式：从 gitea 拉最新代码升级（默认分支 main） |
| `--branch <分支>` | git 方式指定分支 |
| `--repo <地址>` | git 方式指定仓库 |
| `--no-backup` | 跳过升级前自动备份（不推荐） |

升级流程：自动先备份（调 backup.sh）→ 新代码落盘 `releases/` → 切 `current` → `up -d --build` → 健康检查（最多 120s）→ **失败自动回滚**。历史版本保留最近 5 个（`releases/`），`current.old` 是回滚锚点。

### 4.4 常见误用

| 现象 | 原因 | 正确操作 |
|---|---|---|
| `升级版本: .env` 且备份报"未找到 csctalon-db" | 把 bootstrap 的参数（`--env-file` 等）传给了 upgrade.sh，被当成发布包路径 | 用 §4.1 的正确命令，不带多余参数 |
| 备份报"未找到运行中的 csctalon-db 容器" | 服务器服务未启动，或执行机器不对 | `docker compose ps` 确认；未启动先 `docker compose --env-file /opt/csctalon/.env up -d` |
| 安装报"缺少生产关键变量(DB_DATA_DIR/ENV_FILE)" | `/opt/csctalon/.env` 是旧模板生成的（根目录 `.env.example` 无生产目录变量） | 新版 install-server.sh 已改为**自动补齐**目录变量后继续；若提示"仍含占位符密钥"，则需编辑 `.env` 改强随机值再重跑 |

---

## 5. 数据备份（导出）

独立脚本 [backup.sh](backup.sh)，本地=导出迁移包，服务器=日常/升级前备份。备份内容：① PostgreSQL 全库（pg_dump -Fc）；② 后端附件 `backend/uploads`；③ Playwright 登录态 `browser_states`。

```bash
# 默认输出到 ./backups，保留 7 份
./deploy/backup.sh

# 服务器场景：输出到固定目录，读生产 .env 找账号/目录
./deploy/backup.sh --output /opt/csctalon/backups --keep 14 --env-file /opt/csctalon/.env
```

产物：`<output>/csctalon-backup-YYYYmmdd-HHMMSS.tar.gz`

### 5.1 日常自动备份（✅ 生产已配置，2026-09-20 起）

**现状**：生产服务器已落 `/etc/cron.d/csctalon-backup`，**每日 03:30** 自动备份，保留最近 14 份。

```bash
# 查看当前配置
sudo cat /etc/cron.d/csctalon-backup
# 查看备份日志（每次执行追加）
tail -n 50 /var/log/csctalon-backup.log
```

配置内容（`/etc/cron.d/csctalon-backup`，权限 644 root:root）：
```
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * * root /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14 >> /var/log/csctalon-backup.log 2>&1
```

要点：
- 用 `/opt/csctalon/current/` 软链 → **随版本升级自动用最新脚本**，无需改 cron
- `--keep 14` 只清理 `csctalon-backup-*.tar.gz`；**手工 dump**（如 `pre-org-*.dump`、
  `wallet_*.sql.gz`、`ai_reports_*.dump`）不匹配该前缀，**不会被自动清理**
- 日志由 `/etc/logrotate.d/csctalon-backup` 周轮转（保留 4 份、压缩）

**nginx 文件日志轮转（#279）**：容器 stdout 已由 docker json-file 轮转兜底；
`logs/nginx/*.log` 文件日志由宿主 logrotate 日轮转（保留 14 份、压缩）。
安装（一次性，root）：

```bash
sudo cp /opt/csctalon/current/deploy/logrotate.d/csctalon-nginx /etc/logrotate.d/csctalon-nginx
sudo logrotate -d /etc/logrotate.d/csctalon-nginx   # 干跑验证
```

> 配置在仓库版本化（`deploy/logrotate.d/csctalon-nginx`），与 `/etc/cron.d/` 同理可审计；
> 若 `.env` 改了 `NGINX_LOGS_DIR`，配置中的路径必须同步修改。

> 为什么用 `/etc/cron.d/` 而非 `crontab -e`：文件可审计、可版本化、可 `diff`，
> 且带 `PATH` 声明不依赖 root 的默认 PATH。旧文档给出的 `crontab -e` 方式仍可用，
> 但**不要与 `/etc/cron.d/` 重复配置**（会一天跑两次）。

**恢复演练（尚未做，待排期）**：`restore.sh` 在隔离环境走一遍并核对业务表计数，
确认「备份能真的恢复」而不只是「备份能生成」。

**异地保存（强烈建议，尚未配置）**：把 `backups/` 增量 rsync 到另一台机器/对象存储：

```bash
# 需先确认目标机可达与磁盘余量；配好后再加入 /etc/cron.d/
0 4 * * * root rsync -avz --delete /opt/csctalon/backups/ root@<备份机>:/backup/csctalon/
```

---

## 5.5 开发环境从生产取数（sync-from-prod.sh）

本地开发缺数据、生产不便直接联调时，用 [sync-from-prod.sh](sync-from-prod.sh) 在**本地开发机**执行：

```bash
./deploy/sync-from-prod.sh --ssh-prod root@<生产IP> --env-file .env
# 只恢复数据库，不覆盖本地附件/登录态:
./deploy/sync-from-prod.sh --ssh-prod root@<生产IP> --db-only
```

**机制**：① 生产机内跑 `backup.sh`（优先复用 `/opt/csctalon/current` 已部署版本，否则从仓库拉）生成快照；② `rsync` 拉回本地；③ 本地 `pg_restore` 覆盖开发库；④ 数据完整性校验（`users`/`refunds` 计数）。

**🔒 生产只读**：
- 对生产只做读操作（`backup.sh`/`pg_dump`）+ 建/删临时目录；**生产 DB 与 Redis 均不写入**。
- `pg_restore --clean`、`redis-cli FLUSHALL` 等破坏性操作**全部作用在本地开发环境**。
- `--ssh-prod` 误指本机（localhost/127.0.0.1）或生产 db 容器 IP 与本地相同时会**拒绝执行**（双重防误连）。

**⚠️ Redis 无需同步**：本系统 Redis 只有可重建的看板缓存（TTL 自动过期，`cache_warmer` 重建）+ 无状态标记（同步互斥锁在内存/DB），没有必须随库迁移的持久数据。因此脚本**默认不清 Redis**；仅调试需要时加 `--clear-redis` 显式清**本地** Redis。

**注意**：同步源加密凭证由生产 `CONFIG_ENCRYPTION_KEY` 派生，拉回本地后可能无法解密；若解密失败，用本地 `.env` 的 `CONFIG_ENCRYPTION_KEY` 重加密或重填凭证（见 `.env.example` 注释）。

---

## 6. 数据导入（恢复）

独立脚本 [restore.sh](restore.sh)，用于：① 首次部署时把本地迁移数据导入服务器；② 故障后恢复 / 回滚数据。

> ⚠️ 恢复会覆盖当前数据库与附件，恢复到"备份生成时刻"的数据状态，之后的增量数据会丢失。恢复前建议先 `backup.sh` 留现场。

```bash
sudo /opt/csctalon/current/deploy/restore.sh /opt/csctalon/backups/csctalon-backup-xxxx.tar.gz
# 只恢复数据库不覆盖附件
sudo /opt/csctalon/current/deploy/restore.sh <备份包> --db-only
```

流程：停止 web/backend → pg_restore 覆盖库（--clean --if-exists）→ 恢复附件/登录态 → 启动服务 → 健康检查。

---

## 7. 回滚

[rollback.sh](rollback.sh) = 恢复数据到指定备份点（可选）+ 代码切回历史版本。

| 场景 | 操作 |
|---|---|
| 升级后服务不健康 | 已自动回滚（upgrade.sh 内置）。确认后修复再升。 |
| 升级后功能异常需人工回退 | `sudo /opt/csctalon/current/deploy/rollback.sh`（交互式选版本）；数据如需回退到升级前：`--restore backups/升级前备份包` |
| 数据误操作/被删 | `restore.sh` 恢复最近备份。 |

---

## 8. 安全清单（上线前逐项确认）

- [ ] 安全组仅放行 `80`（+未来 `443`）与 `22`（限办公 IP），`5432/6379/8000` 不对公网
- [ ] `.env` 未入 git；密码均为强随机；`JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY` 已按 §3.4 处理
- [ ] 初始 `admin` 已登录并改密
- [ ] `WORKBUDDY_SYNC_TOKEN` 已设置（空则推送接口 503）
- [ ] 每日备份 cron 已配置且成功跑过一次
- [ ] SSH 密钥登录已启用（如可能关闭密码登录）
- [ ] 日志轮转生效（json-file 10m×3 + /opt/csctalon/logs）
- [ ] 无孤儿 Docker 卷 / 发布杂质（2026-08-30 部署审计，见下）

### 8.1 部署巡检与残留清理（2026-08-30 部署审计）

生产勘查确认过 4 类可收敛项，代码侧已修复（发布链路统一清理、`.env` 路径变量统一、PG 属主固定），
以下为**服务器上人工执行一次**的收尾动作：

**① 清理孤儿 Docker 卷**（早期 project 漂移残留，当前容器走 bind mount 不在其中，删掉不影响业务）：

```bash
docker volume rm \
  920f48e_csctalon-pgdata  current_csctalon-pgdata \
  920f48e_csctalon-redisdata current_csctalon-redisdata
```

> 删除前先确认：`docker ps` 中 db/redis 容器挂载是 bind（`docker inspect csctalon-db --format '{{range .Mounts}}{{.Type}} {{.Source}} {{.Destination}}{{end}}'`），
> 且 `/opt/csctalon/data/postgres` 非空（`du -sh /opt/csctalon/data/postgres`）。

**② 删除 `current` 里 git 模式带入的历史杂质目录**（新代码已由 upgrade.sh/install-server.sh 清理，旧 release 残留可删）：

```bash
cd /opt/csctalon/current
rm -rf .git .gitea .dsh .trae .workbuddy docs scripts backend/scripts backend/tests
```

> 若历史 `releases/<版本>` 内也有，一并清理（保留最近 5 个之外会被自动清理）。

**③ 收敛 `.env` 路径变量**：生产 `/opt/csctalon/.env` 应只有一套**单数**路径变量（与后端代码 `settings.UPLOAD_DIR/BROWSER_STATE_DIR` 一致）：
`DB_DATA_DIR / REDIS_DATA_DIR / UPLOAD_DIR / BROWSER_STATE_DIR / BACKEND_LOGS_DIR / NGINX_LOGS_DIR / ENV_FILE`。
若仍有失效的 `UPLOAD_DIR=./uploads`（相对容器内路径）或复数 `UPLOADS_DIR/BROWSER_STATES_DIR`，删掉旧的、保留绝对值。

> 自 2026-08-31 起 `install-server.sh` 与 `upgrade.sh` 内置**上传路径变量守卫**：检测到复数废弃变量
> （`UPLOADS_DIR/BROWSER_STATES_DIR`）或相对/`/app` 开头的单数变量（`UPLOAD_DIR/BROWSER_STATE_DIR`）时
> 会打印黄色告警，提示改为绝对路径（仅告警不自动改，防止覆盖用户配置）。

**④ PG 数据目录属主**：db 容器固定以 uid 999 运行（compose `user: "999:999"`），数据目录应属主 `999:999`
（宿主侧显示为数字 999 而非错位的 `dnsmasq`）。若宿主 root 仍无法遍历：

```bash
chown -R 999:999 /opt/csctalon/data/postgres
```

> 注意：仅当 PG 停止或容器重建时执行，避免运行中改属主引发异常。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 服务启动但 `http://IP/` 打不开 | 安全组是否放行 80；`docker compose ps` 是否全 running；nginx 日志 `logs/nginx/` |
| 升级后登录报"凭证解密失败" | `.env` 的 `JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY` 与迁移前不一致 → 恢复原值重启 |
| 登录一直 401(用户名或密码错误) | 密码记错，或改了 `INIT_ADMIN_PASSWORD` 但**首次建管理员后不生效** → 用 §0⑦ 直接重置 DB 里的 admin 密码 |
| 升级/迁移后系统"数据全空"(业务表全 0 或缺关键表) | 容器连到了新建的空库（数据目录落进了 `releases/<版本>/data` 或相对 `./data/postgres`）→ 先 `du -sh /opt/csctalon/data` + psql 查 `refunds` 计数，用升级前备份 §0④ 恢复 |
| `cd /opt/csctalon/current` 报"符号链接的层数过" | `current`/`current.old` 软链自引用（upgrade.sh 回滚锚点生成 bug）→ 用 `ls -l /opt/csctalon/current` 检查后 `ln -sfn /opt/csctalon/releases/<版本> /opt/csctalon/current` 手工扶正 |
| 恢复后数据不对 | 备份是时间点快照；确认用的是期望时刻的备份包 |
| 同步任务不跑 | `BMS_SCHEDULE_AUTOSTART` 是否为 true；看 `logs/backend/` 同步日志 |
| 同步后当天数据缺失 / `[wallet] 导入完成: 新增` 为负数 | **BMS 页面时间窗口设置失败**（页面保留默认/上次范围，拉到旧流水）。看同步日志：① `设置时间范围: X ~ Y` 是否 = 期望窗口；② 是否有 `[wallet] 疑似时间窗口未设置成功` WARNING；③ `[wallet] 实际入库 N 行 op_time 范围` 是否落在窗口内。均为 2026-09-01 修复项（`timezone_id=Asia/Shanghai` + `fill_utc=False` + ticket placeholder/`.show-more` + `:visible` 非法选择器），确认部署版本含 `f8e3b3d` 之后代码；详见 `docs/standards/数据同步与加工说明.md §4.5` |
| docker build 卡在 Downloading Chrome for Testing | Playwright 浏览器下载走 npmmirror 镜像（Dockerfile 已内置 `PLAYWRIGHT_DOWNLOAD_HOST`），重试构建即可 |

## 10. HTTPS（阿里云免费证书）

前置：**已备案域名 + A 记录已解析到服务器公网 IP**。代码侧配置（nginx 443 + 80 跳转、certs 挂载）已内置，只需按下面步骤落地。

### 10.1 申请证书（阿里云控制台）

1. 控制台 → 数字证书管理服务（SSL 证书）→ 免费证书 → 创建证书 → 申请。
2. 按指引做 DNS 验证（加一条 TXT/CNAME 记录）。
3. 签发后**下载 → 选 Nginx 格式**，得到 `域名.pem` 和 `域名.key`。

### 10.2 上传证书到服务器固定目录

证书放固定目录（不随代码版本切换，升级不会丢），与 data/uploads 同理：

```bash
# 本地
scp 域名.pem 域名.key root@<服务器IP>:/opt/csctalon/certs/

# 服务器：目录不存在则创建
mkdir -p /opt/csctalon/certs
```

### 10.3 修改 .env（两个变量）

```bash
vi /opt/csctalon/.env
```

- `CERTS_DIR=/opt/csctalon/certs`（compose 用 `${CERTS_DIR:-./certs}` 挂载，默认 ./certs，生产建议指到固定目录）
- `CORS_ORIGINS=...` 追加 `https://你的域名`（逗号分隔；CORS 白名单，漏了前端会跨域报错）

### 10.4 改 nginx.conf 证书文件名

[web/nginx.conf](../web/nginx.conf) 已含 443 server + 80 跳转，默认引用 `kf.example.com.pem/.key`。把证书文件名改成你上传的实际文件名（或把证书改名为 `kf.example.com.pem`）：

```nginx
ssl_certificate     /etc/nginx/certs/你的域名.pem;
ssl_certificate_key /etc/nginx/certs/你的域名.key;
```

### 10.5 安全组放行 443

阿里云安全组放行 `443`（HTTPS）到 `0.0.0.0/0`（或办公 IP 段）；`80` 保留用于跳转。

### 10.6 生效并验证

```bash
# 方式 A（git 部署）：本地 push 后服务器升级
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/upgrade.sh | sudo bash -s -- --git

# 方式 B（tar 包）：本地 ./deploy/package.sh + upload.sh + upgrade.sh

# 验证
curl -k https://你的域名/ -o /dev/null -w "%{http_code}\n"     # 200
curl http://你的域名/ -o /dev/null -w "%{http_code}\n"         # 301 跳转
docker compose --env-file /opt/csctalon/.env ps                # web 容器含 0.0.0.0:443->80
```

浏览器访问 `https://你的域名/`，确认锁图标 + 登录功能正常（含上传/同步等跨域调用）。

> 证书到期前一个月记得续期重新上传（免费证书一年期，可开"到期前提醒"）。
> 纯 IP 无域名只能自签名（浏览器告警），不建议生产。
