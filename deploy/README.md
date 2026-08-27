# 客服中心管理系统 · 阿里云部署运维手册

> 版本: v1.2 · 2026-08-24
> 部署方式: 支持 **git 直连一键部署**（推荐）与 **tar 包发布** 两种，单机 Docker Compose，够用且易回滚。
> 目录: `deploy/` 下的脚本 + 本手册，覆盖 **安装 / 部署 / 升级 / 备份 / 数据导入导出 / 回滚 / 数字证书(HTTPS)** 全流程。
>
> 📌 **本手册 = 整体上云（数据在云端）**。若你需要 **数据留内网、计算在本地、外网可访问**，
> 请改用 [deploy/frp/README.md](frp/README.md) 的 **frp 内网穿透方案**（本地为主 + 云端转发），
> 两者可互补：frp 为主部署、云上作为异地备份仓库。

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

### 脚本清单

| 脚本 | 在哪儿执行 | 作用 |
|---|---|---|
| `bootstrap.sh` | 服务器（curl 一条命令） | **git 方式一键安装**：从 gitea 拉代码 → 调 install-server.sh 完成部署 |
| `install-server.sh` | 服务器 | 首次部署：装 Docker → 建目录 → 落盘代码 → 准备 `.env` → 构建启动 → 可选恢复数据 |
| `upgrade.sh` | 服务器 | 升级：自动备份 → 拉新代码 → 重建 → 健康检查（失败自动回滚）；支持 tar 包或 `--git` 两种来源 |
| `rollback.sh` | 服务器 | 回滚：代码切回历史版本 + 可选恢复备份数据 |
| `backup.sh` | 本地或服务器 | **数据导出**：数据库 + 附件 + 登录态（本地=导出迁移包；服务器=日常/升级前备份） |
| `restore.sh` | 服务器 | **数据导入**：从备份包恢复数据库 + 附件，支持 `--db-only` |
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

### 5.1 日常自动备份（建议 cron 每日）

```bash
# 服务器 crontab -e 加入
30 3 * * * /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14 >> /var/log/csctalon-backup.log 2>&1
```

**异地保存（强烈建议）**：把 `backups/` 增量 rsync 到另一台机器/对象存储：

```bash
0 4 * * * rsync -avz --delete /opt/csctalon/backups/ root@<备份机>:/backup/csctalon/
```

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

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 服务启动但 `http://IP/` 打不开 | 安全组是否放行 80；`docker compose ps` 是否全 running；nginx 日志 `logs/nginx/` |
| 升级后登录报"凭证解密失败" | `.env` 的 `JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY` 与迁移前不一致 → 恢复原值重启 |
| 恢复后数据不对 | 备份是时间点快照；确认用的是期望时刻的备份包 |
| 同步任务不跑 | `BMS_SCHEDULE_AUTOSTART` 是否为 true；看 `logs/backend/` 同步日志 |
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
