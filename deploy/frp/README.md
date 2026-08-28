# frp 内网穿透部署方案 — 云端转发 + 本地主数据

> 版本: v2.0 · 2026-08-28
> 定位: 在现有「阿里云/腾讯云整体部署」之外，新增一套 **「本地为主 + 云端转发」** 的部署方式，满足**数据留内网、计算在本地、外网可访问**的核心诉求。
> 目录: `deploy/frp/` 下的模板与脚本。
> v2.0 更新: 全部脚本支持 **git raw 一条命令** 部署（`curl ... | sudo bash -s -- ...`，模板自动从仓库拉取），并补齐参数/配置/运维/排障的完整说明。

---

## 1. 方案概述

### 1.1 架构

```
┌─────────────── 云端转发机 (1C2G 起步) ───────────────┐
│  nginx:443 (TLS 终止, 域名证书)                      │
│    └─ proxy_pass → 127.0.0.1:8080 (frps vhostHTTPPort)│
│  frps 容器 (frp server, 端口 7000 隧道口)             │
│  ⚠️ 不跑任何业务容器, 业务数据零落地云端              │
└──────────────────────┬───────────────────────────────┘
                       │ frp 隧道 (frpc 出站连接, 云端无法主动连入内网)
┌──────────────────────┴───────────────────────────────┐
│ 本地服务器 (csctalon 数据/计算/存储所在地)             │
│  nginx :80 (现有 web 容器, 零改动)                    │
│  backend :8000 / postgres / redis / Playwright        │
│  frpc 客户端 (binary+systemd 或 docker)               │
│  backup.sh + sync-backup-to-cloud.sh (每日异地备份)    │
└──────────────────────────────────────────────────────┘
```

### 1.2 流量路径（一次外网请求的完整链路）

外网用户访问 `https://kf.example.com/` 时的完整链路：

```
用户浏览器
   │ ① DNS 解析 kf.example.com → 云端公网 IP
   ▼
云端 nginx (443 / TLS 终止)
   │ ② 反代 → 127.0.0.1:8080 (frps vhostHTTPPort)
   ▼
frps (云端容器, 只绑 127.0.0.1)
   │ ③ 按 HTTP Host 头(kf.example.com) 匹配代理 → 走 frp 隧道
   ▼ (frp 隧道: 云端 frps ←出站连接← 本地 frpc)
本地 frpc
   │ ④ 转发 → 127.0.0.1:80
   ▼
本地 nginx (web 容器, 与内网访问同一入口)
   │ ⑤ 反代 /api/ → backend:8000
   ▼
csctalon 后端 → PostgreSQL / Redis / Playwright (全部在本地)
```

要点：

- **隧道由 frpc 主动出站建立**，云端无法主动连入内网（天然穿透防火墙/NAT）；
- **业务数据全程在本地**：云端只见加密的 TLS 流量 + 端口转发，业务数据零落地；
- **内网直连不受影响**：内网用户直接访问本地 nginx:80，流量完全不经过 frp，延迟与之前一致。

### 1.3 关键设计

| 关注点 | 方案处理 |
|---|---|
| 数据留在内网 | PG/Redis/uploads/登录态全在本地，云端只做 TLS 终止 + 端口转发，**业务数据不出内网** |
| 频繁更新/高频计算 | 定时同步 8/12/16/20/24 点、Playwright 抓取、分析计算全部跑在本地，不受云端小机器资源限制 |
| 外网访问 | 外网用户 → 域名:443 → 云端 nginx → frps → frp 隧道 → 本地 frpc → 本地 nginx:80，与内网体验一致 |
| 内网直连不受影响 | 内网用户仍直接访问本地 nginx，流量完全不走 frp |
| 成本 | 云端只需 1C2G 轻量机 + 域名证书，比整系统上云便宜得多 |

### 1.4 与现有「整体上云」方案的关系

- **frp 方案（本文件）= 主部署**：数据留内网，云端转发。
- **现有 `deploy/README.md` 的阿里云整体部署 = 灾备**：把 `sync-backup-to-cloud.sh` 的备份推送到云上，本地故障时可快速用 `restore.sh` 在云端恢复整套系统。

两者可共存：**本地是生产，云端既能转发（frp）又能当备份仓库**。

---

## 2. 前置准备

### 2.1 云端服务器

- 腾讯云/阿里云轻量或 CVM，**1C2G 起步**，系统 Ubuntu 22.04/Debian 12。
- 一个**已备案域名**（如 `kf.example.com`），DNS 加一条 **A 记录 → 云端公网 IP**。
- 域名 SSL 证书（腾讯云/阿里云免费证书，Nginx 格式 `.crt` + `.key`）。

### 2.2 本地服务器

- 现有 csctalon 单机 Docker Compose 部署，`web` 容器监听 `80`。
- 建议：UPS 电源 + 断网自恢复（frpc 已配 `Restart=always`/`restart: unless-stopped`）。

### 2.3 端口与安全组清单（云端）

| 端口 | 监听位置 | 用途 | 安全组建议 |
|---|---|---|---|
| `443` | 云端 nginx | HTTPS，对外访问入口 | `0.0.0.0/0` |
| `80` | 云端 nginx | HTTP → 301 跳转 HTTPS | `0.0.0.0/0` |
| `7000` | 云端 frps | frp 隧道口（frpc 出站连接） | **仅办公/内网出口 IP**（一般固定） |
| `8080` | 云端 frps | `vhostHTTPPort`，nginx 反代目标 | **不开放**（只绑 127.0.0.1） |
| `7500` | 云端 frps | Dashboard 面板 | **不开放**（只绑 127.0.0.1，SSH 隧道访问） |
| `5432`/`6379`/`8000` | 本地 | PG/Redis/后端 | **不开放**（本地 compose 已只绑 127.0.0.1） |

> 云端只需放行 `443`/`80`/`7000`（+`22` 限办公 IP）。`8080`/`7500` 都只监听 `127.0.0.1`，公网不可达。

### 2.4 执行方式说明（重要）

`deploy/frp/` 下的脚本都支持 **两种执行方式**，二选一即可：

| 方式 | 适用场景 | 说明 |
|---|---|---|
| **git raw 一条命令** | 服务器能访问 gitea（`10.147.17.41:3000`） | `curl ... \| sudo bash -s -- ...`，配置模板**自动从仓库拉取**，无需克隆/上传 |
| **本地脚本** | 已克隆/上传仓库，或服务器不能访问 gitea | `bash deploy/frp/.../xxx.sh ...`，直接使用仓库内模板 |

git raw 方式的模板来源默认是 gitea `main` 分支：

```
http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main
```

如需指定其他仓库/分支/私有镜像源，给脚本加 `--repo <raw_base>` 覆盖（`<raw_base>` 是能直接 `curl` 到仓库根文件的 base URL）。

> 部署过程会访问：云端执行 `deploy-frps.sh` 需要能访问 **gitea（拉模板）+ get.docker.com（装 Docker）+ GitHub（frps 镜像）**；本地执行 `install-frpc.sh`（binary 模式）需要能访问 **gitea（拉模板）+ GitHub（下载 frp 二进制）**。若网络受限，用本地脚本方式 + 提前上传文件，或用 `--mode docker` 避开二进制下载。

---

## 3. 部署步骤

### 3.1 生成安全 token（两处共用同一值）

```bash
openssl rand -hex 32
# 记下输出, 云端 deploy-frps.sh 与本地 install-frpc.sh 都要用
```

### 3.2 云端：一键部署 frps + nginx

**方式一（推荐）：git raw 一条命令**（无需克隆/上传，模板自动从 gitea 拉取）：

```bash
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/deploy-frps.sh | \
  sudo bash -s -- \
    --domain kf.example.com \
    --token <上一步的token> \
    --dash-pwd <frp面板密码>
```

**方式二：本地脚本**（已克隆/上传仓库时）：

```bash
bash deploy/frp/cloud/deploy-frps.sh \
  --domain kf.example.com \
  --token <上一步的token> \
  --dash-pwd <frp面板密码> \
  --cert /path/to/kf.example.com.crt --key /path/to/kf.example.com.key
```

脚本会：装 Docker/nginx → 生成 `/etc/frp/frps.toml` 与 nginx 站点 → 启动 frps 容器 + nginx → 校验。

> 证书未提前上传时，可先不传 `--cert/--key`，脚本生成占位配置，稍后把证书放到 `/etc/nginx/certs/<域名>.crt/.key` 并 `systemctl reload nginx`。

### 3.3 本地：一键接入 frpc

**方式一（推荐）：git raw 一条命令**：

```bash
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/install-frpc.sh | \
  sudo bash -s -- \
    --server-host <云端公网IP或域名> --server-port 7000 \
    --token <同一token> --domain kf.example.com
```

**方式二：本地脚本**（已克隆/上传仓库时）：

```bash
# 方式 A: binary + systemd (推荐, Linux/macOS 通用)
bash deploy/frp/local/install-frpc.sh \
  --server-host <云端公网IP或域名> --server-port 7000 \
  --token <同一token> --domain kf.example.com

# 方式 B: docker (Linux, 无需下二进制)
bash deploy/frp/local/install-frpc.sh \
  --server-host <云端公网IP或域名> --server-port 7000 \
  --token <同一token> --domain kf.example.com --mode docker
```

> 两种方式均支持 `--repo <raw_base>` 指定模板来源（默认 gitea main 分支）。git raw 方式下模板会自动从仓库拉取，无需本地文件。

### 3.4 本地 CORS 配置（必须，否则外网登录/接口被拦截）

后端按 `CORS_ORIGINS` 校验请求来源。启用 frp 外网访问后，**本地 `.env` 必须把外网域名加进 `CORS_ORIGINS`**，否则外网浏览器请求会被后端拒绝（内网访问不受影响）。

```bash
# 本地 .env:
CORS_ORIGINS=http://<内网IP或域名>,https://kf.example.com   # 多个用英文逗号分隔
```

改完后重启 backend 生效：

```bash
docker compose --env-file /opt/csctalon/.env restart backend
```

> 模板参考 `deploy/env.production.example` 中的 `CORS_ORIGINS` 注释。

### 3.5 验证

```bash
# 本地
curl -sI https://kf.example.com/ | head -1   # 期望 200
# 浏览器打开 https://kf.example.com/ → 应看到 csctalon 登录页
# 手机(外网)打开同一地址 → 同样可访问
# 外网登录一次 + 打开一个看板页面, 确认 API 不被 CORS 拦截
```

### 3.6 备份/恢复与迁移

- **异地备份**（强烈建议，见 §6）：每日 `sync-backup-to-cloud.sh` 把 `backup.sh` 产物推到云端。
- **整机迁移/灾备**：用现有 `deploy/README.md` 的 `backup.sh` → 云端 `restore.sh` 可把整套系统恢复到云端（当本地整体故障时）。

---

## 4. 脚本与参数参考

三个脚本均可通过 `--help` 查看内嵌用法（git raw 管道方式同样可用）。

### 4.1 deploy-frps.sh（云端，frp 服务端 + nginx TLS）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--domain <域名>` | ✅ | — | 对外访问域名（与 DNS、本地 customDomains、nginx server_name 一致） |
| `--token <token>` | ✅ | — | frp 认证 token（与本地 frpc **完全一致**，`openssl rand -hex 32`） |
| `--dash-pwd <密码>` | ✅ | — | frp Dashboard 面板密码 |
| `--cert <证书>` | 选 | — | nginx 证书 `.crt` 路径；不传则生成占位配置 |
| `--key <私钥>` | 选 | — | nginx 私钥 `.key` 路径；须与 `--cert` 成对 |
| `--repo <raw_base>` | 选 | gitea main | 模板来源 git raw base |
| `--frp-version <v>` | 选 | `0.61.1` | frps 镜像版本 |
| `--vhost-port <端口>` | 选 | `8080` | frps `vhostHTTPPort`（须与云端 nginx 反代目标一致） |
| `--skip-install-docker` | 选 | 关 | 已装 Docker 时跳过安装 |

**脚本做的事**：装 Docker/nginx → 生成 `/etc/frp/frps.toml` → 处理证书（`/etc/nginx/certs/`）→ 生成 `/etc/nginx/conf.d/csctalon-frp.conf` → 启动 frps 容器（`--network host`）→ 重启 nginx → 输出公网 IP 与后续指引。

### 4.2 install-frpc.sh（本地，frp 客户端接入）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--server-host <地址>` | ✅ | — | 云端 frps 地址（公网 IP 或域名） |
| `--server-port <端口>` | 选 | `7000` | 云端 frps 端口 |
| `--token <token>` | ✅ | — | frp 认证 token（与云端 frps **完全一致**） |
| `--domain <域名>` | ✅ | — | 对外访问域名（与云端 nginx server_name 一致） |
| `--mode <binary\|docker>` | 选 | `binary` | binary+systemd（通用）/ docker（Linux 免下二进制） |
| `--install-dir <路径>` | 选 | `/opt/csctalon/frpc` | frpc 配置/二进制/compose 落盘目录 |
| `--frp-version <v>` | 选 | `0.61.1` | 二进制版本（binary 模式） |
| `--repo <raw_base>` | 选 | gitea main | 模板来源 git raw base |

**脚本做的事**：生成 `<install-dir>/frpc.toml`（替换 serverAddr/token/domain 占位符）→ binary 模式下载 frp 二进制并安装 systemd 服务 `frpc.service`（`Restart=always`）；docker 模式把 compose 落到 install-dir 并 `docker compose up -d`。

> **docker 模式说明**：`--mode docker` 使用 `docker-compose.frpc.yml`（`snowdreamtech/frpc:<version>` 镜像 + `network_mode: host`）。host 网络仅 **Linux** 支持，macOS/Windows 的 Docker 请用 binary 模式。两种模式产物都在 `--install-dir`（默认 `/opt/csctalon/frpc`），便于 `docker compose -f ... restart` 或 `systemctl restart frpc` 复用。

### 4.3 sync-backup-to-cloud.sh（本地，异地备份）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--dest <用户@主机:路径>` | ✅ | — | 推送目标（如 `root@1.2.3.4:/backup/csctalon`） |
| `--env-file <路径>` | 选 | 自动探测 | `.env` 路径（优先 `/opt/csctalon/.env`，回退当前目录） |
| `--output <目录>` | 选 | 仓库根/`backups` | 本地备份输出目录 |
| `--keep <N>` | 选 | `7` | 本地/云端保留备份份数 |
| `--ssh-key <密钥>` | 选 | — | 指定 SSH 私钥（用于 rsync/ssh 到云端） |
| `--no-local-backup` | 选 | 关 | 跳过本地 `backup.sh`，只推送已有备份 |
| `--prune` | 选 | 关 | 同步后清理云端多余旧备份（与 keep 一致） |
| `--repo <raw_base>` | 选 | gitea main | `backup.sh` 来源 git raw base |

**脚本做的事**：定位/拉取 `backup.sh` → 生成最新备份包（若未 `--no-local-backup`）→ 校验存在备份包 → `rsync -avz` 增量推送到云端 → 可选 `--prune` 清理云端旧备份。

---

## 5. 配置详解

### 5.1 frps.toml（云端，`/etc/frp/frps.toml`）

| 项 | 值 | 说明 |
|---|---|---|
| `bindPort` | `7000` | frpc 连接口，安全组只对办公 IP 开放 |
| `auth.token` | 强随机 | 与 frpc 一致 |
| `vhostHTTPPort` | `8080` | 云端 nginx 反代目标；**只绑 127.0.0.1** |
| `webServer` | `127.0.0.1:7500` | 面板仅本机，SSH 隧道访问 |
| `log.to` | `/var/log/frps/frps.log` | 日志，轮转保留 7 天 |

### 5.2 frpc.toml（本地）

| 项 | 值 | 说明 |
|---|---|---|
| `serverAddr` | 云端公网 IP/域名 | frpc 出站连云端 |
| `serverPort` | `7000` | 与云端 bindPort 一致 |
| `auth.token` | 强随机 | 与云端 frps 一致 |
| `transport.heartbeatInterval/Timeout` | `10`/`30` | 心跳与断线自动重连 |
| `proxies[].type` | `http` | HTTP 代理，按 Host 分流 |
| `proxies[].localIP/localPort` | `127.0.0.1:80` | 本地 web 容器 nginx |
| `proxies[].customDomains` | 域名 | 与云端 nginx server_name 一致 |

### 5.3 本地方案不动现有 compose

frpc 是**独立进程/容器**，只读 `127.0.0.1:80`，对 `docker-compose.yml`（web/backend/db/redis）零改动。升级/回滚照旧用 `upgrade.sh`/`rollback.sh`。

---

## 6. 备份与灾备（必做）

### 6.1 每日异地备份

**方式一（推荐）：git raw 一条命令**（cron 用，无需本地代码目录，模板自动从仓库拉取）：

```bash
# 本地 crontab -e 加入:
30 3 * * * curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/sync-backup-to-cloud.sh | \
  sudo bash -s -- \
    --dest root@<云端IP>:/backup/csctalon \
    --env-file /opt/csctalon/.env \
    --output /opt/csctalon/backups --keep 14 \
    >> /var/log/csctalon-syncbackup.log 2>&1
```

**方式二：本地脚本**（已部署仓库时，与现有 `backup.sh` cron 风格一致）：

```bash
# 本地 crontab -e 加入:
30 3 * * * bash /opt/csctalon/current/deploy/frp/local/sync-backup-to-cloud.sh \
  --dest root@<云端IP>:/backup/csctalon \
  --env-file /opt/csctalon/.env \
  --output /opt/csctalon/backups --keep 14 \
  >> /var/log/csctalon-syncbackup.log 2>&1
```

脚本内部：先 `backup.sh` 生成最新备份包 → `rsync -avz` 增量推到云端 → 可选清理云端旧备份。

### 6.2 恢复

```bash
# 从云端拉回备份包:
rsync -avz root@<云端IP>:/backup/csctalon/csctalon-backup-*.tar.gz ./
# 本地恢复(覆盖当前数据):
sudo bash /opt/csctalon/current/deploy/restore.sh ./csctalon-backup-xxx.tar.gz
```

---

## 7. 日常运维

### 7.1 查看 frp 状态

```bash
# 云端 frps 容器
ssh root@<云端IP> "docker ps --filter name=frps --format '{{.Names}} {{.Status}}'"
# 本地 frpc (binary 模式)
systemctl status frpc
journalctl -u frpc -f          # 实时日志
# 本地 frpc (docker 模式)
docker ps --filter name=csctalon-frpc
docker logs -f csctalon-frpc
# 云端 frp Dashboard(SSH 隧道访问, 不对外暴露)
ssh -L 7500:127.0.0.1:7500 root@<云端IP>
# 浏览器打开 http://127.0.0.1:7500  (admin / <--dash-pwd 的值>)
```

### 7.2 修改 frp 配置（token/端口等）

```bash
# 云端: 编辑 /etc/frp/frps.toml → 重启 frps 容器
vi /etc/frp/frps.toml
ssh root@<云端IP> "docker restart frps"
# 本地(binary): 编辑 <install-dir>/frpc.toml → 重启服务
vi /opt/csctalon/frpc/frpc.toml
systemctl restart frpc
# 本地(docker): 编辑 <install-dir>/frpc.toml → 重启容器
vi /opt/csctalon/frpc/frpc.toml
docker restart csctalon-frpc
```

> 注意：token 是云端 frps 与本地 frpc 的共享密钥，**改了任一端必须同步改另一端**，否则隧道认证失败（见 §9 排障）。

### 7.3 更换证书 / 更新 frp 版本

```bash
# 证书: 替换 /etc/nginx/certs/<域名>.crt/.key → 云端 reload nginx
ssh root@<云端IP> "cp 新证书 /etc/nginx/certs/ && nginx -t && systemctl reload nginx"
# frp 版本: 云端重跑 deploy-frps.sh --frp-version <新版本>(幂等, 会重建容器)
#          本地 binary 重跑 install-frpc.sh --frp-version <新版本>(重新下载并重启)
```

### 7.4 升级 csctalon 业务代码

frpc 是独立组件，**业务升级不受影响**，照常用现有流程：

```bash
# 本地: 现有 upgrade.sh / rollback.sh 完全不变
sudo bash /opt/csctalon/current/deploy/upgrade.sh <发布包>   # 或 --git
```

---

## 8. 安全清单（上线前逐项确认）

- [ ] 云端安全组仅放行 `443`/`7000`(限办公IP)/`22`(限办公IP)，`8080/7500/5432/6379` 不开放
- [ ] frp token 为强随机（`openssl rand -hex 32`），云端/本地一致
- [ ] frp 面板密码已设置，面板仅本机监听
- [ ] 域名 HTTPS 证书已配置并强制 301 跳转
- [ ] 本地 `.env` 的 `CORS_ORIGINS` 已包含 `https://<域名>`（后端校验来源）
- [ ] 每日异地备份 cron 已配置且成功跑过一次
- [ ] 本地 UPS + 断网自恢复就绪

---

## 9. 常见问题

| 现象 | 排查 |
|---|---|
| 外网打不开域名 | ① 云端 frps 容器是否 running；② 安全组 7000/443 是否放行；③ 本地 frpc 是否在线（`systemctl status frpc`/`docker ps`）；④ DNS 是否指向云端 IP |
| 本地正常、外网 502 | frpc 隧道断了：`journalctl -u frpc -f`（binary）或 `docker logs -f csctalon-frpc`（docker）看是否报 **auth 失败**（token 不一致）/ **连接失败**（7000 不通） |
| 外网登录报 CORS / 接口被拦 | 本地 `.env` 的 `CORS_ORIGINS` 没加 `https://<域名>`，或加了没重启 backend（见 §3.4） |
| 报 `auth token not match` | 云端 frps 与本地 frpc 的 token 不一致。重跑对应脚本用同一 token，或改其中一端后同步另一端（见 §7.2） |
| 上传/导出大文件很慢 | 受本地**上行带宽**限制（家宽上行通常 20-50Mbps）。内网用户不受影响；可评估升级宽带套餐 |
| 域名打不开但 IP 能开 | 证书/server_name 未对齐，`nginx -t` + 检查 `/etc/nginx/conf.d/csctalon-frp.conf` |
| 本地断电恢复后外网仍不通 | frpc `Restart=always`/`restart: unless-stopped` 会自动重连，等 10-30s；仍不行 `systemctl restart frpc`（binary）/`docker restart csctalon-frpc`（docker） |
| docker 模式提示 `network_mode: host` 不支持 | host 网络仅 Linux 支持；macOS/Windows 的 Docker 请用 `--mode binary` |
| 想访问 frp Dashboard | 不要对公网开放 7500；用 SSH 隧道 `ssh -L 7500:127.0.0.1:7500 root@<云端IP>` 后访问 `http://127.0.0.1:7500` |
| git raw 方式报「拉取模板失败」 | 确认服务器能访问 gitea（`curl -sI http://10.147.17.41:3000`）；或改用本地脚本方式 / `--repo` 指定可达源 |

---

## 10. 文件清单

```
deploy/frp/
├── README.md                     # 本手册
├── cloud/
│   ├── frps.toml                 # frp 服务端配置模板
│   ├── nginx.conf                # 云端 nginx TLS 配置模板
│   └── deploy-frps.sh            # 云端一键部署脚本(支持 git raw 一条命令)
└── local/
    ├── frpc.toml                 # frp 客户端配置模板
    ├── docker-compose.frpc.yml   # frpc docker 方式
    ├── install-frpc.sh           # 本地一键接入脚本(支持 git raw 一条命令)
    └── sync-backup-to-cloud.sh   # 异地备份脚本(支持 git raw 一条命令)
```

> 所有脚本均支持两种执行方式：**git raw 一条命令**（`curl ... | sudo bash -s -- ...`，模板自动从仓库拉取）与**本地脚本**（已克隆/上传仓库）。git raw 模式默认从 gitea `main` 分支拉取模板，可用 `--repo <raw_base>` 覆盖来源。
