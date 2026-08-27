# frp 内网穿透部署方案 — 云端转发 + 本地主数据

> 版本: v1.0 · 2026-08-27
> 定位: 在现有「阿里云/腾讯云整体部署」之外，新增一套 **「本地为主 + 云端转发」** 的部署方式，满足**数据留内网、计算在本地、外网可访问**的核心诉求。
> 目录: `deploy/frp/` 下的模板与脚本。

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

**关键设计**：

| 关注点 | 方案处理 |
|---|---|
| 数据留在内网 | PG/Redis/uploads/登录态全在本地，云端只做 TLS 终止 + 端口转发，**业务数据不出内网** |
| 频繁更新/高频计算 | 定时同步 8/12/16/20/24 点、Playwright 抓取、分析计算全部跑在本地，不受云端小机器资源限制 |
| 外网访问 | 外网用户 → 域名:443 → 云端 nginx → frps → frp 隧道 → 本地 frpc → 本地 nginx:80，与内网体验一致 |
| 内网直连不受影响 | 内网用户仍直接访问本地 nginx，流量完全不走 frp |
| 成本 | 云端只需 1C2G 轻量机 + 域名证书，比整系统上云便宜得多 |

### 1.2 与现有「整体上云」方案的关系

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

### 2.3 安全组/防火墙放行（云端）

| 端口 | 用途 | 建议 |
|---|---|---|
| `443` | HTTPS | `0.0.0.0/0` |
| `7000` | frp 隧道口 | **仅办公/内网出口 IP**（frpc 出站连接，一般固定） |
| `22` | SSH | 仅办公 IP |
| `8080`/`7500`/`5432`/`6379` | frps vhost/面板等 | **不开放**（已绑 127.0.0.1） |

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

### 3.4 验证

```bash
# 本地
curl -sI https://kf.example.com/ | head -1   # 期望 200
# 浏览器打开 https://kf.example.com/ → 应看到 csctalon 登录页
# 手机(外网)打开同一地址 → 同样可访问
```

### 3.5 备份/恢复与迁移

- **异地备份**（强烈建议，见 §5）：每日 `sync-backup-to-cloud.sh` 把 `backup.sh` 产物推到云端。
- **整机迁移/灾备**：用现有 `deploy/README.md` 的 `backup.sh` → 云端 `restore.sh` 可把整套系统恢复到云端（当本地整体故障时）。

---

## 4. 配置详解

### 4.1 frps.toml（云端，`/etc/frp/frps.toml`）

| 项 | 值 | 说明 |
|---|---|---|
| `bindPort` | `7000` | frpc 连接口，安全组只对办公 IP 开放 |
| `auth.token` | 强随机 | 与 frpc 一致 |
| `vhostHTTPPort` | `8080` | 云端 nginx 反代目标；**只绑 127.0.0.1** |
| `webServer` | `127.0.0.1:7500` | 面板仅本机，SSH 隧道访问 |

### 4.2 frpc.toml（本地）

| 项 | 值 | 说明 |
|---|---|---|
| `serverAddr` | 云端公网 IP/域名 | frpc 出站连云端 |
| `proxies[].type` | `http` | HTTP 代理，按 Host 分流 |
| `proxies[].localIP/localPort` | `127.0.0.1:80` | 本地 web 容器 nginx |
| `proxies[].customDomains` | 域名 | 与云端 nginx server_name 一致 |

### 4.3 本地方案不动现有 compose

frpc 是**独立进程/容器**，只读 `127.0.0.1:80`，对 `docker-compose.yml`（web/backend/db/redis）零改动。升级/回滚照旧用 `upgrade.sh`/`rollback.sh`。

---

## 5. 备份与灾备（必做）

### 5.1 每日异地备份

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

### 5.2 恢复

```bash
# 从云端拉回备份包:
rsync -avz root@<云端IP>:/backup/csctalon/csctalon-backup-*.tar.gz ./
# 本地恢复(覆盖当前数据):
sudo bash /opt/csctalon/current/deploy/restore.sh ./csctalon-backup-xxx.tar.gz
```

---

## 6. 安全清单（上线前逐项确认）

- [ ] 云端安全组仅放行 `443`/`7000`(限办公IP)/`22`(限办公IP)，`8080/7500/5432/6379` 不开放
- [ ] frp token 为强随机（`openssl rand -hex 32`），云端/本地一致
- [ ] frp 面板密码已设置，面板仅本机监听
- [ ] 域名 HTTPS 证书已配置并强制 301 跳转
- [ ] 本地 `.env` 的 `CORS_ORIGINS` 已包含 `https://<域名>`（后端校验来源）
- [ ] 每日异地备份 cron 已配置且成功跑过一次
- [ ] 本地 UPS + 断网自恢复就绪

---

## 7. 常见问题

| 现象 | 排查 |
|---|---|
| 外网打不开域名 | ① 云端 frps 容器是否 running；② 安全组 7000/443 是否放行；③ 本地 frpc 是否在线（`systemctl status frpc`/`docker ps`）；④ DNS 是否指向云端 IP |
| 本地正常、外网 502 | frpc 隧道断了：`journalctl -u frpc -f` 看是否报 auth 失败（token 不一致）/连接失败（7000 不通） |
| 上传/导出大文件很慢 | 受本地**上行带宽**限制（家宽上行通常 20-50Mbps）。内网用户不受影响；可评估升级宽带套餐 |
| 域名打不开但 IP 能开 | 证书/server_name 未对齐，`nginx -t` + 检查 `/etc/nginx/conf.d/csctalon-frp.conf` |
| 本地断电恢复后外网仍不通 | frpc `Restart=always` 会自动重连，等 10-30s；仍不行 `systemctl restart frpc` |

---

## 8. 文件清单

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
