# 客服中心管理系统 · 阿里云部署运维手册

> 版本: v1.0 · 2026-08-24
> 部署方式: 本地 tar 打包上传 → 服务器 Docker Compose 构建运行（单机，够用且易回滚）
> 目录: `deploy/` 下的脚本 + 本手册，覆盖 **发布 / 升级 / 回滚 / 备份 / 恢复** 全流程。

---

## 1. 体系总览

```
本地开发机                         阿里云服务器 (/opt/csctalon)
─────────────                      ─────────────────────────────
deploy/package.sh 打包  ──┐
deploy/upload.sh  上传  ──┼──▶ packages/      ← 发布包 tar.gz
deploy/backup.sh  导出   ──┘   releases/<版本>/ ← 每版代码(解压产物)
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
- **升级即切版本**：新包解压到独立目录 → 切软链 → `docker compose up -d --build` 重建。后端启动自动执行 `alembic upgrade head`（幂等），**数据库结构无需手动迁移**。
- **备份即整个数据目录**：PG 用 `pg_dump -Fc`（可恢复、体积小）+ 附件/登录态打成一个 tar.gz，恢复一条命令。
- **免重复同步**：本地库已是全量数据，用 `backup.sh` 导出 → 服务器 `install --restore` 导入，同步配置（含加密凭证）随库一起迁移。

### 脚本清单

| 脚本 | 在哪儿执行 | 作用 |
|---|---|---|
| `package.sh` | 本地 | 打发布包 `dist/csctalon-<版本>-<时间戳>.tar.gz` + `latest.tar.gz` |
| `upload.sh` | 本地 | 发布包 scp 上传到服务器 `packages/` |
| `install-server.sh` | 服务器 | 首次部署：装 Docker → 解包 → 准备 `.env` → 构建启动 → 可选 `--restore` 导入数据 |
| `upgrade.sh` | 服务器 | 升级：自动备份 → 切新版本 → 重建 → 健康检查（失败自动回滚） |
| `rollback.sh` | 服务器 | 回滚：代码切回历史版本 + 可选恢复备份数据 |
| `backup.sh` | 本地或服务器 | 数据库+附件备份（本地=导出迁移包；服务器=日常/升级前备份） |
| `restore.sh` | 服务器 | 恢复备份包（数据库 + 附件），支持 `--db-only` |

---

## 2. 首次部署（阿里云）

### 2.1 前置

- 一台阿里云 ECS（建议 2C4G 起），系统 Ubuntu 22.04/Debian 12。
- 安全组放行：`80`（HTTP）；`22`（SSH，仅办公 IP）；**不要**放行 `5432/6379/8000`（compose 已只绑 127.0.0.1）。
- 本地与服务器 SSH 免密可达（含 SD-WAN 环境）。

### 2.2 本地：打包 + 上传 + 导出数据

```bash
cd /path/to/csctalon

# 1) 打发布包（自动取 git 版本号，可传自定义版本：./deploy/package.sh v1.0.0）
./deploy/package.sh

# 2) 导出本地全量数据（数据库+附件+登录态）→ ./backups/csctalon-backup-*.tar.gz
./deploy/backup.sh

# 3) 上传发布包与数据备份到服务器
./deploy/upload.sh root@<服务器IP>
scp ./backups/csctalon-backup-*.tar.gz root@<服务器IP>:/opt/csctalon/packages/
```

### 2.3 服务器：准备 .env（关键）

```bash
# 首次先手动建目录、解包以取得 env 模板（或直接参考 deploy/env.production.example）
mkdir -p /opt/csctalon/packages && cd /opt/csctalon/packages
tar xzf csctalon-*.tar.gz -C /tmp/csctalon_pkg
cp /tmp/csctalon_pkg/deploy/env.production.example /opt/csctalon/.env
vi /opt/csctalon/.env
```

`.env` 必改项（模板顶部有详细注释）：

- `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `INIT_ADMIN_PASSWORD` / `WORKBUDDY_SYNC_TOKEN`：改强密码。
- **`JWT_SECRET_KEY` / `CONFIG_ENCRYPTION_KEY`：直接沿用本地 `backend/.env` 的值**（否则同步源加密凭证解不开，就真的要重新同步/重录了）。详见模板头注释的轮换步骤。
- `CORS_ORIGINS=http://<公网IP或域名>`。
- 六个目录变量（`DB_DATA_DIR` 等）保持模板默认（/opt/csctalon/...），install 脚本会创建。

### 2.4 服务器：安装并导入数据

```bash
cd /opt/csctalon/packages && tar xzf csctalon-*.tar.gz -C /tmp/csctalon_pkg
sudo bash /tmp/csctalon_pkg/deploy/install-server.sh \
  --pkg /opt/csctalon/packages/csctalon-*.tar.gz \
  --env-file /opt/csctalon/.env \
  --restore /opt/csctalon/packages/csctalon-backup-*.tar.gz
```

> `--restore` 会在服务启动后调用 restore.sh 导入数据库与附件。完成后直接 `http://<服务器IP>/` 登录，**数据已在、同步配置已在，无需再跑同步**。

### 2.5 验证

```bash
docker compose --env-file /opt/csctalon/.env ps   # 四个容器 healthy
curl http://127.0.0.1:8000/health                 # 200
```

浏览器登录 → 看今日/运营/决策看板数据是否为本地那份 → 数据采集页跑一次增量确认同步源可用。

---

## 3. 版本发布流程（发版规范）

1. **本地**：代码改完、测试通过 → `git tag vX.Y.Z && git push`（gitea，SD-WAN 可达）。
2. **本地**：`./deploy/package.sh vX.Y.Z` 生成带版本号的发布包。
3. **本地**：`./deploy/upload.sh root@<IP> dist/csctalon-vX.Y.Z-*.tar.gz`。
4. **服务器**：`sudo bash /opt/csctalon/packages/csctalon-vX.Y.Z-*/deploy/upgrade.sh /opt/csctalon/packages/csctalon-vX.Y.Z-*.tar.gz`。
   - 升级前自动备份到 `backups/`；
   - 后端启动自动跑 alembic 迁移；
   - 健康检查不过自动回滚，无需人工干预。
5. 升级后确认页面功能正常。

> 版本号规范：`v<主>.<次>.<补丁>`。发版时在 `git tag` 上写 changelog，方便回溯。
> 可选：把 2~4 步写成一个本地脚本（`deploy/release.sh <tag> <user@host>`）一键发布，需要时再加。

---

## 4. 升级

```bash
sudo /opt/csctalon/current/deploy/upgrade.sh /opt/csctalon/packages/csctalon-v1.1.0-*.tar.gz
```

- 自动先备份 → 解压新包到 `releases/` → 切 `current` → `up -d --build` → 健康检查。
- 失败自动切回旧版本并重建。
- 历史版本保留最近 5 个（`releases/`），`current.old` 是回滚锚点。

## 5. 备份与恢复

### 5.1 日常备份（建议 cron 每日）

```bash
# crontab -e 加入（服务器）
30 3 * * * /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14 >> /var/log/csctalon-backup.log 2>&1
```

产物：`/opt/csctalon/backups/csctalon-backup-YYYYmmdd-HHMMSS.tar.gz`（数据库 dump + 附件 + 登录态）。

**异地保存（强烈建议）**：把 `backups/` 增量 rsync 到另一台机器/对象存储：

```bash
# 可再加一条 cron
0 4 * * * rsync -avz --delete /opt/csctalon/backups/ root@<备份机>:/backup/csctalon/
```

### 5.2 恢复

```bash
sudo /opt/csctalon/current/deploy/restore.sh /opt/csctalon/backups/csctalon-backup-xxxx.tar.gz
# 只恢复数据库不碰附件: 追加 --db-only
```

> 恢复 = 回到备份生成时刻。之后的新数据会丢，恢复前先 backup.sh 留现场。

### 5.3 恢复演练

每季度至少做一次：在**测试环境**（同架构另一台机或本机副本）跑 install → restore → 登录核对数据，确保备份可用。

---

## 6. 回滚

| 场景 | 操作 |
|---|---|
| 升级后服务不健康 | 已自动回滚（upgrade.sh 内置）。确认后修复新包再升。 |
| 升级后功能异常需人工回退 | `sudo /opt/csctalon/current/deploy/rollback.sh`（选历史版本）；数据如需回退到升级前：`--restore backups/升级前备份包` |
| 数据误操作/被删 | `restore.sh` 恢复最近备份。 |

---

## 7. 安全清单（上线前逐项确认）

- [ ] 安全组仅放行 `80`（+未来 `443`）与 `22`（限办公 IP），`5432/6379/8000` 不对公网
- [ ] `.env` 未入 git；密码均为强随机；`JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY` 已按 §2.4 处理
- [ ] 初始 `admin` 已登录并改密
- [ ] `WORKBUDDY_SYNC_TOKEN` 已设置（空则推送接口 503）
- [ ] 每日备份 cron 已配置且成功跑过一次
- [ ] SSH 密钥登录已启用（如可能关闭密码登录）
- [ ] 日志轮转生效（json-file 10m×3 + /opt/csctalon/logs）

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| 服务启动但 `http://IP/` 打不开 | 安全组是否放行 80；`docker compose ps` 是否全 running；nginx 日志 `logs/nginx/` |
| 升级后登录报"凭证解密失败" | `.env` 的 `JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY` 与迁移前不一致 → 恢复原值重启 |
| 恢复后数据不对 | 备份是时间点快照；确认用的是期望时刻的备份包 |
| 同步任务不跑 | `BMS_SCHEDULE_AUTOSTART` 是否为 true；看 `logs/backend/` 同步日志 |

## 9. HTTPS（可选，后续再加）

当前为 IP+HTTP。需要域名时：申请证书 → `certs/` 挂载 → nginx.conf 加 443 server + 80 跳转，参考 `docs/腾讯云部署迁移方案.md` §5.1 的配置片段（`deploy/` 下已留好改造入口）。
