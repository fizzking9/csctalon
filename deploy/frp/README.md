# frp 内网穿透部署方案 — 云端转发 + 本地主数据

> 版本: v2.1 · 2026-08-30
> 定位: 在现有「阿里云/腾讯云整体部署」之外，新增一套 **「本地为主 + 云端转发」** 的部署方式，满足**数据留内网、计算在本地、外网可访问**的核心诉求。
> 目录: `deploy/frp/` 下的模板与脚本。
> v2.1 更新: 新增统一 `docker-compose.yml`（profile 区分 frps/frpc，双端共用，见 §3.4）。
> v2.0 更新: 全部脚本支持 **git raw 一条命令** 部署（`curl ... | sudo bash -s -- ...`，模板自动从仓库拉取），并补齐参数/配置/运维/排障的完整说明。

---

## 1. 方案概述

### 1.1 架构

```
┌─────────────── 云端转发机 (1C2G 起步) ───────────────┐
│  1Panel + OpenResty:443 (TLS 终止, 证书自动签发)      │
│    └─ proxy_pass → 127.0.0.1:8080 (frps vhostHTTPPort)│
│  frps 容器 (frp server, 端口 7000 隧道口)             │
│  ⚠️ 不跑任何业务容器, 业务数据零落地云端              │
└──────────────────────┬───────────────────────────────┘
                       │ frp 隧道 (frpc 出站连接, 云端无法主动连入内网)
┌──────────────────────┴───────────────────────────────┐
│ 本地服务器 (csctalon 数据/计算/存储所在地)             │
│  nginx :80 (现有 web 容器, 零改动)                    │
│  backend :8000 / postgres / redis / Playwright        │
│  frpc 客户端 (binary+systemd 或 docker compose)      │
│  backup.sh + sync-backup-to-cloud.sh (每日异地备份)    │
└──────────────────────────────────────────────────────┘
```

### 1.2 流量路径（一次外网请求的完整链路）

外网用户访问 `https://kf.example.com/` 时的完整链路：

```
用户浏览器
   │ ① DNS 解析 kf.example.com → 云端公网 IP
   ▼
云端 1Panel + OpenResty (443 / TLS 终止, 证书由 1Panel 自动签发/续期)
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
| 频繁更新/高频计算 | 定时同步 8-23/0 每小时 + 5:00 补跑 + 8:00 全量兜底、Playwright 抓取、分析计算全部跑在本地，不受云端小机器资源限制 |
| 外网访问 | 外网用户 → 域名:443 → 云端 1Panel OpenResty → frps → frp 隧道 → 本地 frpc → 本地 nginx:80，与内网体验一致 |
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
- 安装 **1Panel**（推荐，证书自动签发/续期）：云端由 1Panel 的 **OpenResty** 承担 443 TLS 终止 + 反代 frps。
- 一个**已备案域名**（如 `kf.example.com`），DNS 加一条 **A 记录 → 云端公网 IP**（Let's Encrypt 验证需要）。
- 域名 SSL 证书：**不用自己申请**，1Panel 网站→SSL 里选 Let's Encrypt 即可（免费、自动续期）。
- 备选：不想用 1Panel 时，`deploy-frps.sh --mode nginx` 走原方案（宿主 nginx + 手动证书，见 §3.2 方式三）。

### 2.2 本地服务器

- 现有 csctalon 单机 Docker Compose 部署，`web` 容器监听 `80`。
- 建议：UPS 电源 + 断网自恢复（frpc 已配 `Restart=always`/`restart: unless-stopped`）。

### 2.3 端口与安全组清单（云端）

| 端口 | 监听位置 | 用途 | 安全组建议 |
|---|---|---|---|
| `443` | 云端 1Panel OpenResty | HTTPS，对外访问入口 | `0.0.0.0/0` |
| `80` | 云端 1Panel OpenResty | HTTP → 301 跳转 HTTPS | `0.0.0.0/0` |
| `7000` | 云端 frps | frp 隧道口（frpc 出站连接） | **仅办公/内网出口 IP**（一般固定） |
| `8080` | 云端 frps | `vhostHTTPPort`，1Panel OpenResty 反代目标 | **不开放**（只绑 127.0.0.1） |
| `7500` | 云端 frps | Dashboard 面板 | **不开放**（只绑 127.0.0.1，SSH 隧道访问） |
| `5432`/`6379`/`8000` | 本地 | PG/Redis/后端 | **不开放**（本地 compose 已只绑 127.0.0.1） |

> 云端只需放行 `443`/`80`/`7000`（+`22` 限办公 IP）。`8080`/`7500` 都只监听 `127.0.0.1`，公网不可达。
> ⚠️ 若云端口被 1Panel 的 OpenResty 占用，不要与其它服务抢 80/443；frps 的 8080 保持只绑 127.0.0.1 即可。

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

### 3.2 云端：一键部署 frps + TLS 入口

云端 TLS 入口**默认走 1Panel OpenResty**（证书自动签发/续期），脚本只负责部署 frps 容器并打印反代配置。

**方式一（推荐）：1Panel OpenResty（默认）**

先装好 **1Panel**（1Panel 自带 OpenResty；如果 1Panel 里没有，先到 1Panel「应用商店」安装 OpenResty 并启动）。

再执行 git raw 一条命令（无需克隆/上传，模板自动从 gitea 拉取）：

```bash
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/deploy-frps.sh | \
  sudo bash -s -- \
    --domain kf.example.com \
    --token <上一步的token> \
    --dash-pwd <frp面板密码>
```

脚本会：装 Docker → 生成 `/etc/frp/frps.toml` → 启动 frps 容器 → **打印 1Panel OpenResty 反代配置**。

然后到 1Panel 界面完成 TLS 入口：

1. **1Panel → 网站 → 网站** → 添加（或反向代理）：绑定域名 `kf.example.com`。
2. **SSL** → 申请证书 → **Let's Encrypt**（免费，自动续期；需要域名已解析到本机公网 IP）。
3. 把脚本打印的反代配置粘贴到该网站的 **配置文件** 中（替换默认内容；注意 `proxy_pass http://127.0.0.1:8080;`，8080 即 frps 的 vhostHTTPPort）。
4. 保存后 1Panel 自动 reload OpenResty 生效。

**方式二：1Panel 已就绪，只生成配置不启动 frps**

```bash
# 先让 1Panel/OpenResty 把 80/443 接管好，再启动 frps
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/deploy-frps.sh | \
  sudo bash -s -- --domain kf.example.com --token <token> --dash-pwd <密码> \
    --no-install-docker --skip-frps
```

**方式三：不用 1Panel，宿主 nginx（原方案）**

```bash
bash deploy/frp/cloud/deploy-frps.sh \
  --domain kf.example.com \
  --token <上一步的token> \
  --dash-pwd <frp面板密码> \
  --mode nginx \
  --cert /path/to/kf.example.com.crt --key /path/to/kf.example.com.key
```

脚本会：装 Docker/nginx → 生成 `/etc/frp/frps.toml` 与 nginx 站点 → 启动 frps 容器 + nginx → 校验。

> 方式三证书未提前上传时，可先不传 `--cert/--key`，脚本生成占位配置，稍后把证书放到 `/etc/nginx/certs/<域名>.crt/.key` 并 `systemctl reload nginx`。

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

### 3.4 docker compose 方式（统一文件，双端共用，推荐）

如果你两端都已装 docker，可以用**同一份** `deploy/frp/docker-compose.yml`，用 **profile** 区分服务端/客户端，一份文件两端各自启动：

```bash
# ① 分别准备好 toml 配置（从仓库模板复制并替换占位符）：
#    服务端: cp cloud/frps.toml ./frps.toml   → 替换 REPLACE_FRP_TOKEN / REPLACE_FRP_DASH_PWD
#    客户端: cp local/frpc.toml ./frpc.toml   → 替换 REPLACE_FRP_SERVER_HOST / REPLACE_FRP_TOKEN / REPLACE_FRP_DOMAIN

# ② 在各自机器上启动（无需整个仓库，docker-compose.yml + 对应 toml 两个文件即可）：
#    云端转发机：
mkdir -p /var/log/frps
FRPS_CONFIG=./frps.toml docker compose --profile frps up -d

#    本地服务器：
FRPC_CONFIG=./frpc.toml docker compose --profile frpc up -d
```

**国内拉镜像超时怎么办（Docker Hub 连不上）**：

```bash
# 推荐: 给 Docker 配加速器, 然后用官方镜像名直接拉(无需改 compose)
#   1Panel: 容器→设置→加速器→添加 https://docker.1panel.live 保存(自动重启 Docker)
#   或 /etc/docker/daemon.json 写 registry-mirrors 后 systemctl restart docker
# 然后直接启动即可(compose 走加速器拉取):
FRPS_CONFIG=./frps.toml docker compose --profile frps up -d
```

> 统一 compose 默认镜像：frps = `snowdreamtech/frps:0.71.0-alpine`、frpc = `snowdreamtech/frpc:0.71.0-alpine`（注意仓库名是 **frps/frpc**，不是 `frp`）。若确需指定其他镜像源，用 `FRP_IMAGE` 覆盖（需与本地已拉取的镜像名完全一致）。

常用运维（两端各自用自己 profile）：

```bash
# 改配置后重启 / 看日志 / 停止
# 服务端: docker compose --profile frps restart | logs -f frps | down
# 客户端: docker compose --profile frpc restart | logs -f csctalon-frpc | down
```

> 说明：
> - 统一 compose 使用 `network_mode: host`（frp 需绑宿主端口/访问宿主 `127.0.0.1:80`），仅 **Linux** 支持；macOS/Windows 的 Docker 请用 §3.2/§3.3 的二进制方式。
> - 配置路径可用环境变量覆盖：服务端 `FRPS_CONFIG`（默认 `./cloud/frps.toml`）、客户端 `FRPC_CONFIG`（默认 `./local/frpc.toml`）。若已用脚本生成过配置，可直接 `FRPS_CONFIG=/etc/frp/frps.toml` 或 `FRPC_CONFIG=/opt/csctalon/frpc/frpc.toml` 复用，无需再生成。
> - 镜像可用 `FRP_IMAGE` 覆盖（默认 frps=`snowdreamtech/frps:0.71.0-alpine`、frpc=`snowdreamtech/frpc:0.71.0-alpine`，仓库名是 frps/frpc 不是 frp）。国内拉 Docker Hub 超时优先配加速器（见上方示例）；`/var/log/frps` 需宿主持有（`mkdir -p /var/log/frps`），否则 frps 日志目录写不进去。
> - 云端端口安全同上：只放行 `443/80/7000`；`8080`/`7500` 均只绑 `127.0.0.1`（host 网络下 frps.toml 里 `webServer.addr = "127.0.0.1"` 已保证）。

### 3.5 本地 CORS 配置（必须，否则外网登录/接口被拦截）

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

### 3.6 验证

```bash
# 本地
curl -sI https://kf.example.com/ | head -1   # 期望 200
# 浏览器打开 https://kf.example.com/ → 应看到 csctalon 登录页
# 手机(外网)打开同一地址 → 同样可访问
# 外网登录一次 + 打开一个看板页面, 确认 API 不被 CORS 拦截
```

### 3.7 备份/恢复与迁移

- **异地备份**（强烈建议，见 §6）：每日 `sync-backup-to-cloud.sh` 把 `backup.sh` 产物推到云端。
- **整机迁移/灾备**：用现有 `deploy/README.md` 的 `backup.sh` → 云端 `restore.sh` 可把整套系统恢复到云端（当本地整体故障时）。

---

## 4. 脚本与参数参考

三个脚本均可通过 `--help` 查看内嵌用法（git raw 管道方式同样可用）。

### 4.1 deploy-frps.sh（云端，frp 服务端 + TLS 入口）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--domain <域名>` | ✅ | — | 对外访问域名（与 DNS、本地 customDomains、TLS 入口 server_name 一致） |
| `--token <token>` | ✅ | — | frp 认证 token（与本地 frpc **完全一致**，`openssl rand -hex 32`） |
| `--dash-pwd <密码>` | ✅ | — | frp Dashboard 面板密码 |
| `--mode <openresty\|nginx>` | 选 | `openresty` | TLS 入口模式：`openresty`=1Panel OpenResty 反代（默认，证书自动签发）；`nginx`=宿主 apt nginx 承担 443（原方案） |
| `--cert <证书>` | 选 | — | nginx 模式证书 `.crt` 路径；不传则生成占位配置 |
| `--key <私钥>` | 选 | — | nginx 模式私钥 `.key` 路径；须与 `--cert` 成对 |
| `--repo <raw_base>` | 选 | gitea main | 模板来源 git raw base |
| `--frp-version <v>` | 选 | `0.61.1` | frps 镜像版本 |
| `--vhost-port <端口>` | 选 | `8080` | frps `vhostHTTPPort`（须与 TLS 入口反代目标一致） |
| `--skip-install-docker` | 选 | 关 | 已装 Docker 时跳过安装 |
| `--skip-frps` | 选 | 关 | 不启动 frps 容器（仅生成 TLS 入口配置，供 1Panel OpenResty 先行接管） |

**openresty 模式（默认）做的事**：装 Docker → 生成 `/etc/frp/frps.toml` → 启动 frps 容器（`--network host`）→ **打印 1Panel OpenResty 反代配置**（用户粘贴到 1Panel 网站配置文件，证书在 1Panel 内申请 Let's Encrypt）。

**nginx 模式做的事**：装 Docker/nginx → 生成 `/etc/frp/frps.toml` → 处理证书（`/etc/nginx/certs/`）→ 生成 `/etc/nginx/conf.d/csctalon-frp.conf` → 启动 frps 容器（`--network host`）→ 重启 nginx → 输出公网 IP 与后续指引。

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

### 4.3 cleanup-frps.sh / cleanup-frpc.sh（清理）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--purge` | 选 | 关 | 彻底删除：云端删 `/etc/frp`+`/var/log/frps`+镜像；本地删安装目录+镜像。默认只停服务保留数据 |
| `--compose-dir <目录>` | 选 | 自动探测 | 云端专用，统一 compose 所在目录（当前目录或 `/opt/csctalon/frp`） |
| `--install-dir <路径>` | 选 | `/opt/csctalon/frpc` | 本地专用，frpc 安装目录 |
| `--repo <raw_base>` | 选 | gitea main | 模板来源 git raw base |

**cleanup-frps.sh（云端）做的事**：删 frps 容器（旧 `docker run` 与统一 compose 两种方式）→ `--purge` 时删 `/etc/frp`、`/var/log/frps`、frps 镜像。**不处理** 1Panel 反代/证书/防火墙（需手动，见 §8.5）。

**cleanup-frpc.sh（本地）做的事**：停用并删 `frpc.service`（binary 方式）+ 删 `csctalon-frpc` 容器（docker 方式）→ `--purge` 时删 `/opt/csctalon/frpc` 与 frpc 镜像。**不处理** `.env` CORS 与备份 cron（需手动，见 §8.5）。

### 4.4 sync-backup-to-cloud.sh（本地，异地备份）

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
| `vhostHTTPPort` | `8080` | 1Panel OpenResty 反代目标；**只绑 127.0.0.1** |
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
| `proxies[].customDomains` | 域名 | 与云端 TLS 入口 server_name 一致 |

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

### 6.3 开发环境从生产取数

本地开发缺数据、联调需生产快照时（frp 方案下生产=本地内网机）：

```bash
# 在开发机执行，生产机跑 backup.sh 生成快照 → 拉回本地 pg_restore（生产只读）
bash deploy/sync-from-prod.sh --ssh-prod root@<生产IP> --env-file .env
```

详见 [deploy/README.md §5.5](../README.md#55-开发环境从生产取数sync-from-prodsh)。

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

> 注意：token 是云端 frps 与本地 frpc 的共享密钥，**改了任一端必须同步改另一端**，否则隧道认证失败（见 §10 排障）。

### 7.3 更换证书 / 更新 frp 版本

```bash
# 证书(1Panel OpenResty 模式): 证书由 1Panel 自动签发/续期, 无需手动换
#   → 1Panel 网站→SSL 里查看/续期即可; 若手动换, 直接覆盖后 OpenResty reload
# 证书(宿主 nginx 模式): 替换 /etc/nginx/certs/<域名>.crt/.key → 云端 reload nginx
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
- [ ] 域名 HTTPS 证书已配置（1Panel 自动签发 Let's Encrypt）并强制 301 跳转
- [ ] 本地 `.env` 的 `CORS_ORIGINS` 已包含 `https://<域名>`（后端校验来源）
- [ ] 每日异地备份 cron 已配置且成功跑过一次
- [ ] 本地 UPS + 断网自恢复就绪

---

## 9. 卸载 / 清理（不再需要 frp 时）

> 两个一键清理脚本：**云端** `cleanup-frps.sh`、**本地** `cleanup-frpc.sh`，均支持 git raw 一条命令，默认**只停服务不删数据**（安全），加 `--purge` 才彻底删除。

**云端（frps）**：

```bash
# 方式一: git raw 一条命令
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/cleanup-frps.sh | \
  sudo bash -s -- --purge
# 方式二: 本地脚本
sudo bash deploy/frp/cloud/cleanup-frps.sh --purge
```

脚本会：删 frps 容器（旧 `docker run` 与统一 compose 两种方式都处理）→ `--purge` 时删 `/etc/frp`、`/var/log/frps`、frps 镜像。

**本地（frpc）**：

```bash
# binary+systemd 或 docker 两种方式都会被处理
curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/cleanup-frpc.sh | \
  sudo bash -s -- --purge
# 或本地脚本
sudo bash deploy/frp/local/cleanup-frpc.sh --purge
```

脚本会：停用并删 `frpc.service`（binary）→ 删 `csctalon-frpc` 容器（docker）→ `--purge` 时删 `/opt/csctalon/frpc` 与 frpc 镜像。

**脚本不自动处理、需手动收尾的事项**：

| 位置 | 事项 | 操作 |
|---|---|---|
| 云端 | 1Panel OpenResty 反代站点 + 证书 | 1Panel 网站 → 停用/删除绑定域名的反代站点；证书在 网站→SSL 里删除 |
| 云端 | 防火墙/安全组 | 不再需要 frp 时 `ufw deny 7000`、`ufw deny 8080`，并删安全组对应放行规则 |
| 云端 | 兜底配置(若加过) | 删除 `openresty-default-server.conf` 兜底站点(见 §12.5) |
| 本地 | `.env` 的 `CORS_ORIGINS` | 移除 `https://<域名>` 后 `docker compose --env-file /opt/csctalon/.env restart backend`(不删也不影响内网) |
| 本地 | 备份 cron | 停掉或改目标 `sync-backup-to-cloud.sh`(§6.1), 否则会持续报错/推送到已停的服务 |

> 清理后外网域名立即不可达是**预期行为**（说明隧道已断开）；内网直连不受影响。

---

## 10. 常见问题

| 现象 | 排查 |
|---|---|
| 外网打不开域名 | ① 云端 frps 容器是否 running；② 安全组 7000/443 是否放行；③ 本地 frpc 是否在线（`systemctl status frpc`/`docker ps`）；④ DNS 是否指向云端 IP；⑤ 1Panel OpenResty 反代是否已保存（反代目标 `127.0.0.1:8080`） |
| 本地正常、外网 502 | frpc 隧道断了：`journalctl -u frpc -f`（binary）或 `docker logs -f csctalon-frpc`（docker）看是否报 **auth 失败**（token 不一致）/ **连接失败**（7000 不通） |
| 外网登录报 CORS / 接口被拦 | 本地 `.env` 的 `CORS_ORIGINS` 没加 `https://<域名>`，或加了没重启 backend（见 §3.5） |
| 报 `auth token not match` | 云端 frps 与本地 frpc 的 token 不一致。重跑对应脚本用同一 token，或改其中一端后同步另一端（见 §7.2） |
| 上传/导出大文件很慢 | 受本地**上行带宽**限制（家宽上行通常 20-50Mbps）。内网用户不受影响；可评估升级宽带套餐 |
| 域名打不开但 IP 能开 | 证书/server_name 未对齐。1Panel OpenResty 模式：检查 1Panel 网站绑定域名与证书是否匹配；宿主 nginx 模式：`nginx -t` + 检查 `/etc/nginx/conf.d/csctalon-frp.conf` |
| 1Panel 里申请 Let's Encrypt 失败 | 域名 A 记录是否已解析到本机公网 IP、80 端口公网可达（HTTP 验证需要）；否则改用 DNS 验证（1Panel 支持阿里云/腾讯云 DNS API） |
| 本地断电恢复后外网仍不通 | frpc `Restart=always`/`restart: unless-stopped` 会自动重连，等 10-30s；仍不行 `systemctl restart frpc`（binary）/`docker restart csctalon-frpc`（docker） |
| docker 模式提示 `network_mode: host` 不支持 | host 网络仅 Linux 支持；macOS/Windows 的 Docker 请用 `--mode binary` |
| 想访问 frp Dashboard | 不要对公网开放 7500；用 SSH 隧道 `ssh -L 7500:127.0.0.1:7500 root@<云端IP>` 后访问 `http://127.0.0.1:7500` |
| git raw 方式报「拉取模板失败」 | 确认服务器能访问 gitea（`curl -sI http://10.147.17.41:3000`）；或改用本地脚本方式 / `--repo` 指定可达源 |

---

## 11. 文件清单

```
deploy/frp/
├── README.md                     # 本手册
├── docker-compose.yml            # 统一 compose(profile 区分 frps/frpc, 双端共用, 见 §3.4)
├── cloud/
│   ├── frps.toml                 # frp 服务端配置模板
│   ├── openresty.conf            # 1Panel OpenResty 反代配置模板(默认模式, 由脚本打印)
│   ├── openresty-default-server.conf  # 1Panel OpenResty 兜底配置: 拦截未匹配域名/IP 访问, 不暴露 frp
│   ├── nginx.conf                # 宿主 nginx TLS 配置模板(--mode nginx)
│   ├── deploy-frps.sh            # 云端一键部署脚本(支持 git raw 一条命令)
│   └── cleanup-frps.sh           # 云端一键清理脚本(--purge 彻底删除, 见 §9)
└── local/
    ├── frpc.toml                 # frp 客户端配置模板
    ├── docker-compose.frpc.yml   # frpc docker 方式
    ├── install-frpc.sh           # 本地一键接入脚本(支持 git raw 一条命令)
    ├── cleanup-frpc.sh           # 本地一键清理脚本(--purge 彻底删除, 见 §9)
    └── sync-backup-to-cloud.sh   # 异地备份脚本(支持 git raw 一条命令)
```

> 所有脚本均支持两种执行方式：**git raw 一条命令**（`curl ... | sudo bash -s -- ...`，模板自动从仓库拉取）与**本地脚本**（已克隆/上传仓库）。git raw 模式默认从 gitea `main` 分支拉取模板，可用 `--repo <raw_base>` 覆盖来源。

---

## 12. 实战部署实录与排障 FAQ（2026-08-29）

> 本节基于真实部署过程整理，覆盖从「一键脚本」到「域名 HTTPS 可访问」全流程遇到的坑与解法。
> 场景：云端 1Panel + OpenResty（默认 openresty 模式），本地 frpc，域名 `www.kimirock.top`（阿里云备案），云端 `42.193.247.117`（腾讯云）。

### 12.1 部署全流程（推荐顺序）

```
① 云端装 1Panel + 启动 OpenResty（应用商店）
② 生成 token（openssl rand -hex 32，两端共用）
③ 云端执行 deploy-frps.sh（默认 openresty 模式）→ 启动 frps 容器 + 打印 1Panel 反代配置
④ 1Panel → 网站 → 反向代理 绑定域名 → SSL 申请证书（详见 §12.4）→ 粘贴反代配置
⑤ 本地执行 install-frpc.sh → frpc 连上云端 7000
⑥ 云端 ufw deny 8080 + 安全组收紧（7000 限办公 IP）
⑦ 本地 .env 加 CORS_ORIGINS 并重启 backend
⑧ 浏览器验证 https://<域名>
```

> ⚠️ **代码需先推送到 gitea**：`deploy-frps.sh` 走 git raw 从 gitea main 拉取，**本地提交后必须先 `git push`**，云端才能拿到最新版。否则云端拿到旧脚本（如旧版装宿主 nginx + 旧 `http2` 指令），会报 `unknown directive "http2"`。

### 12.2 排障 FAQ（按现象）

| 现象 | 根因 | 解决 |
|---|---|---|
| frpc 报 `dial tcp <云端IP>:7000: i/o timeout` | **云端 UFW/安全组没放行 7000**（frps 在 `*:7000` 监听≠公网可达，UFW 默认 deny 入站） | 云端 `ufw allow 7000/tcp`（建议收紧到办公 IP）；并确认云安全组放行 7000 |
| frpc 日志 `proxy [web-http] already exists` | 云端 frps 残留了旧的同名代理（上次连接没清干净） | 云端 `docker restart frps` 清残留 → 本地 `systemctl restart frpc`；若有多个 frpc 进程残留 `pkill -f frpc` 再重启 |
| 直接输 IP 访问 → frp 提示页/404 | frp 按 **Host 头分流**，IP 的 Host 匹配不到 `customDomains`，落到 frps 兜底 | **预期行为**。正规用 `https://<域名>`；不想暴露 frp → 用 `openresty-default-server.conf` 兜底（return 444/自定义 403）+ `ufw deny 8080`（见 §12.5） |
| 1Panel 申请 Let's Encrypt 报 `x509: cannot parse IP address of length 0` | 1Panel/acme 预检域名解析异常；且 **HTTP 验证在 frp 架构下必失败**（验证请求带域名 Host 被反代进隧道，返回 SPA 而非挑战文件） | **改用 DNS 验证**（阿里云/腾讯云 DNS API），或**换 ZeroSSL 签发机构**（本次实战 ZeroSSL 可走通） |
| 证书申请报 `DomainRecordDuplicate: The DNS record already exists` | 之前申请失败时残留的 `_acme-challenge` TXT 记录没删 | 去阿里云/腾讯云 DNS 控制台**删除残留 `_acme-challenge` TXT 记录**，再重新申请 |
| 证书验证通过但 Safari 打不开 / Edge 能开 | ① Safari 缓存了旧连接/失败状态 ② 腾讯云备案拦截（见下条） | 先 Cmd+Q 退出 Safari / 隐私窗口 / 清网站数据；仍不行查备案拦截 |
| 访问显示「网站暂时无法访问 / 您的网站未完成备案」 | **备案与接入商绑定**：域名在阿里云备案，但服务器在腾讯云，腾讯云对未做**接入备案**的域名强制拦截 | 在**腾讯云**做**接入备案**（控制台→网站备案→接入备案），审核通过后解除拦截。或把服务器换到备案同厂商 |
| 本地 `curl https://127.0.0.1/ -H "Host: 域名"` 200、公网打不开 | 拦截/网络发生在**云厂商公网入口**，对服务器本机回环不生效 | 在**外部机器**（手机流量/本地）测 `https://<域名>`；若被拦则多为备案拦截或安全组 |
| `curl -I -H "Host: <域名>" http://<云端IP>/` 返回 200，但 `http://<IP>/` 404 | frp 按 Host 分流：带域名 Host 才匹配代理 | 用域名访问（或加 `customDomains` 里 IP，但不建议） |
| 外网 502/504 | frpc 隧道断了（token 不一致 / 7000 不通 / 本地 nginx 没起） | 见 §10 与 §7.2 |
| 页面间歇性加载不全/静态资源 404（OpenResty error.log 报 `limiting connections by zone "perip"`） | **1Panel 自动生成的站点配置带按 IP 限并发** `limit_conn perip 25`，办公 NAT 出口多人共用 IP 时并发打满，`/assets/*.js|css` 被掐断 | 调大或对 `/assets/` 豁免，见 §12.3 |
| 部署脚本报旧版 `http2` 指令错误 | **云端拉到了 gitea 旧版脚本** | 本地 `git push` 后再重跑；旧脚本残留的宿主 nginx 需停用（`systemctl stop/disable nginx`，必要时 `apt-get remove nginx`） |

### 12.3 OpenResty `perip` 限流（按 IP 并发）调优

**现象**：OpenResty error.log 出现 `limiting connections by zone "perip"`，被拦的往往是**同段办公出口 NAT IP**（多人共用一个公网 IP），请求是页面加载瞬间的 `/assets/*.js`、`*.css` 并发；表现为页面间歇性白屏/样式丢失/JS 加载不全。

**根因**：1Panel 给每个站点自动生成的配置里默认带按 IP 并发限制（主配置只定义 zone，站点配置里才引用）：

```nginx
# /usr/local/openresty/nginx/conf/nginx.conf(http 块, 仅定义 zone, 不用改)
limit_conn_zone $binary_remote_addr zone=perip:10m;
limit_conn_zone $server_name zone=perserver:10m;

# /usr/local/openresty/nginx/conf/conf.d/www.kimirock.top.conf(站点配置, 限制在这里)
limit_conn perserver 300;    # 整个域名并发上限
limit_conn perip 25;         # ← 每个 IP 并发上限, 办公 NAT 下太紧
```

**定位**：进 OpenResty 容器搜引用位置

```bash
docker exec -it 1Panel-openresty-iTSM sh
grep -rn "limit_conn perip\|limit_conn perserver\|limit_req" /usr/local/openresty/nginx/conf/
# → conf.d/www.kimirock.top.conf:19  limit_conn perserver 300;
# → conf.d/www.kimirock.top.conf:20  limit_conn perip 25;
```

**解决（二选一）**：

1. **简单调大**（推荐先做）：`limit_conn perip 25` → `100`（办公 NAT 后几十人共用 IP 足够，仍能挡扫描器）
   ```bash
   docker exec -it 1Panel-openresty-iTSM sh
   sed -i 's/limit_conn perip 25;/limit_conn perip 100;/' \
     /usr/local/openresty/nginx/conf/conf.d/www.kimirock.top.conf
   nginx -t && nginx -s reload
   ```

2. **静态资源豁免**（更精细，可选）：在站点 server 块加 `/assets/` 专属 location，用更大额度 zone 覆盖继承的 `perip` 限制，让 JS/CSS 永不限流、`/api/` 保持 25 防攻击：
   ```nginx
   # http 块先定义(与 perip 同级):
   #   limit_conn_zone $binary_remote_addr zone=perip_static:10m;
   location /assets/ {
       limit_conn perip_static 1000;      # 覆盖继承的 perip, 静态资源近乎不限制
       proxy_pass http://127.0.0.1:8080;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 900s;
       client_max_body_size 200m;
   }
   ```

> ⚠️ **注意**：`conf.d/www.kimirock.top.conf` 是 1Panel 网站管理自动生成的站点配置，**在 1Panel 网站界面再点保存/改站点设置时可能被重新生成覆盖**。想一劳永逸请在 1Panel 面板内该站点的「配置文件」入口里改；改文件方式则注意别在面板里再动该站点。
>
> 另：error.log 里 `GET /.bashrc`、`GET /api/settings`（海外 IP 段）这类是**公网扫描器探测**，被 `perip` 拦下是**预期且有益**的，不用管。

### 12.4 证书申请（frp 架构下）推荐做法

1. **首选 ZeroSSL + DNS 验证**（本次实战验证可行）：
   - 1Panel → 网站 → SSL → 申请证书 → 签发机构 **ZeroSSL**、验证方式 **DNS**、DNS 厂商选 **阿里云/腾讯云**（填 API 密钥）。
   - DNS-01 走 API 加 TXT 记录，**完全绕开 frp 隧道**，不受 Host 分流影响。
2. **Let's Encrypt**：HTTP-01 在 frp 架构下会被反代进隧道导致验证失败，**不推荐**；除非给 `/.well-known/acme-challenge/` 单独配不走隧道的位置（较麻烦）。
3. 申请失败 `DomainRecordDuplicate` → 删残留 `_acme-challenge` TXT 记录后重试。

### 12.5 不暴露 frp 的兜底配置（直接输 IP 时）

直接输 IP 会落到 frps 默认提示页（暴露 frp 组件信息）。处理：

```bash
# ① 云端封掉 frps vhostHTTPPort 的公网直连（防 frps 兜底被探到）
ufw deny 8080

# ② 在 1Panel OpenResty 加 default_server 兜底（见 openresty-default-server.conf）
#    拦掉未匹配 Host 的请求：return 444（断开连接）或自定义 403 页
#    注意：只拦 80 即可覆盖"输 IP"场景；443 兜底需配占位自签名证书，且不能影响正常站点
```

> 参考文件：`deploy/frp/cloud/openresty-default-server.conf`（含完整配置与说明）。

### 12.6 域名/证书相关核对清单

- DNS A 记录：`www`（和可选 `@`）→ 云端公网 IP（本地 `dig +short www.<域名>` 确认）。
- 三处域名一致：DNS、本地 frpc `customDomains`、云端 OpenResty server_name。
- 证书链完整（Safari 校验严格）：`echo \| openssl s_client -connect <域名>:443 -showcerts 2>/dev/null \| grep -E "^ 0 s:|^ 1 s:|^ 2 s:"` 应有 叶子+中间+根 三层。
- 若想裸域名也可访问：本地 `frpc.toml` 的 `customDomains` 加 `"<裸域名>"`，1Panel 同步绑定 + 证书覆盖。

### 12.7 上线收尾（必做）

1. **换 token**（若曾在对话/日志暴露）：两端一起改，重启 frps + frpc。
2. **本地 `.env` 的 `CORS_ORIGINS`** 加 `https://<域名>` 并重启 backend（否则外网登录被 CORS 拦）。
3. **UFW deny 8080** + 7000 收紧到办公 IP + 安全组仅放 443/80/7000/22。
4. 确认登录限流、`INIT_ADMIN_PASSWORD` 已改、WAF（1Panel）已开。
