# P0-1 凭证泄露事件处置手册

## 事件概述

- **泄露文件**: `backend/scripts/fix_scrm_configs_baiyin_final.py`
- **泄露提交**: `ed3ddcc` (单提交, 已确认)
- **泄露内容**:
  1. 白音 SCRM 后台管理员手机号 + 登录密码 (`PW_USERNAME` / `PW_PASSWORD`)
  2. SCRM OpenAPI `ClientId` / `ClientSecret`
- **当前状态**: 代码侧已修复(改读环境变量 `SCRM_WEB_USERNAME`/`SCRM_WEB_PASSWORD`/`SCRM_CLIENT_ID`/`SCRM_CLIENT_SECRET`, 移除明文打印), 但 **git 历史仍含明文凭证**, 且 **凭证在外部平台仍有效**.

## 处置步骤(按顺序执行, 不可跳步)

### 步骤 1: 立即在 SCRM 平台侧轮换 4 个凭证(最优先)

代码清理无法让已泄露的凭证失效, 必须先在平台侧轮换:

1. 登录白音 SCRM 后台 `https://scrm.baiyinqifu.com` → 修改管理员账号密码.
2. 在 OpenAPI 网关 `https://gateway.bilinl.com` → 重新生成 `ClientId` / `ClientSecret`.
3. 将新凭证写入生产 `.env` (不提交):
   ```
   SCRM_WEB_USERNAME=<新手机号>
   SCRM_WEB_PASSWORD=<新密码>
   SCRM_CLIENT_ID=<新ClientId>
   SCRM_CLIENT_SECRET=<新ClientSecret>
   ```
4. 重跑 `python backend/scripts/fix_scrm_configs_baiyin_final.py` 把新凭证加密灌入 `sync_source_configs` 表.

### 步骤 2: git 历史重写(清除 ed3ddcc 中的明文凭证)

> ⚠️ 重写会改变 ed3ddcc 及其后所有提交的 hash, 需 `git push --force`, 不可逆.
> 已获用户授权(单提交代价低). 执行前请确认无其他人在此期间推送.

#### 2.1 安装 git-filter-repo

```bash
# macOS
brew install git-filter-repo
# 或 pip
pip install git-filter-repo
```

#### 2.2 创建替换规则文件

在仓库根目录创建 `/tmp/creds-replace.txt`, 每行 `旧值==>新值`:

```
<手机号>==><手机号>
!*2026Bl0727#==><密码>
<ClientId>==><ClientId>
<ClientSecret>==><ClientSecret>
```

> 注意: 密码 `<密码>` 含 `#`, 在替换文件中需完整写出(包括 `#@`), 避免被当作注释.

#### 2.3 执行重写(在仓库根目录)

```bash
cd /Users/tanzk/dev/csctalon

# 先备份(保险)
git bundle create /tmp/csctalon-backup-$(date +%Y%m%d).bundle --all

# 重写: 在所有提交历史中替换上述字符串
git filter-repo --replace-text /tmp/creds-replace.txt

# filter-repo 会移除 origin 远程, 重新添加
git remote add origin <你的远程地址>
```

#### 2.4 验证重写结果

```bash
# 确认历史中已无明文凭证
git log --all -p | rg "<手机号>|2026Bl0727|<ClientId>|<ClientSecret>"
# 期望: 无输出(若仍有输出说明替换规则未覆盖, 需补全 /tmp/creds-replace.txt 后重跑)

# 确认当前工作区代码仍正常(应已是改读环境变量的版本)
git log --oneline -5
```

#### 2.5 强制推送

```bash
git push --force origin main
# 若有其他分支/标签也需同步
git push --force --tags origin
```

#### 2.6 通知协作者

- 所有克隆过此仓库的人必须重新克隆, 旧克隆的 history 仍含凭证.
- 若仓库托管在 Gitea/GitLab, 在管理后台触发一次 GC/仓库重打包, 清理服务端旧对象.

### 步骤 3: 后续防护(防止再次发生)

1. **安装 gitleaks 预提交钩子**:
   ```bash
   # 安装 gitleaks: brew install gitleaks
   # 仓库已提供配置 .gitleaks.toml
   cp scripts/install-gitleaks-hook.sh /tmp/ && bash /tmp/install-gitleaks-hook.sh
   ```
2. **CI 集成**(M11 一并补): 在 Gitea Actions 中加 `gitleaks detect` 步骤, PR 扫描失败即阻断合并.
3. **复核**: `backend/scripts/` 下其他一次性脚本是否也有硬编码凭证(已扫描 `fix_scrm_configs_baiyin_final.py`, 建议对整个 `scripts/` 目录跑一次 `gitleaks detect`).

## 验证清单

- [ ] SCRM 平台 4 个凭证已轮换, 旧凭证已失效
- [ ] 生产 `.env` 已更新为新凭证
- [ ] `fix_scrm_configs_baiyin_final.py` 已用新凭证重跑, DB 中凭证已刷新
- [ ] `git log --all -p` 中无明文凭证
- [ ] `git push --force` 完成, 远程历史已清理
- [ ] 协作者已重新克隆
- [ ] gitleaks 预提交钩子已安装
