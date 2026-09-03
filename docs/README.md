# docs 目录组织与命名规范

**生效日期**：2026-09-03
**版本**：v2.0（2026-09-03 升级：加入类型前缀 + 日期时间戳，启动存量归位）
**适用范围**：`docs/` 下所有文档的命名与放置位置（新增强制，存量按本规范归位）
**性质**：强制约束

> 背景：docs 根目录历史文档中英文混杂、报告/规范/设计/说明混放、无日期前缀，检索与维护困难。v2.0 起统一为「目录按类型 + 文件按类型前缀 + 带日期时间戳」。

---

## 一、目录职责（按类型分目录）

| 目录 | 类型 | 命名格式 | 示例 |
|---|---|---|---|
| `docs/arch/` | 已锁定架构设计 | `A-YYYYMMDD-序号-简述.md` | `A-20260903-001-issue-automation-gates.md` |
| `docs/arch/_pending/` | 待审核设计草案 | 同 arch 编号 | `A-20260903-002-xxx.md`（PENDING） |
| `docs/standards/` | 规范/清单/模板 | 英文 kebab-case | `error-code.md`、`issue-workflow.md` |
| `docs/reports/` | 审计/审查/审核报告 | `YYYYMMDD-主题-类型.md` | `20260831-看板缓存优化-审计报告.md` |
| `docs/debug/` | 调试会话/故障归档 | `debug-<主题>-<YYYYMMDD>.md` | `debug-cs-record-gap-20260902.md` |
| `docs/security/` | 安全事件/凭证整改 | kebab-case | `credential-incident-remediation.md` |
| `docs/ops/` | 运维手册/SOP | `<主题>-运维手册.md` | `数据采集-运维手册.md` |
| `docs/design/` | 未锁定设计/方案（评审中） | `<主题>-<YYYYMMDD>.md` | `投诉数据接入设计.md`（若未锁定） |
| `docs/` 根目录 | 全局总纲/索引 | 中文名或 README | `README.md`、`规划设计.md` |

> 规则：**报告进 reports，规范进 standards，手册进 ops，设计进 design（锁定后进 arch），调试进 debug**。

---

## 二、命名规则（统一格式：`类型前缀 + 主题 + 日期时间戳`）

### 2.1 通用规则

1. **带时间戳的文档**（报告/审计/调试/设计）→ 文件名末尾含 `YYYYMMDD` 日期。
2. **不带时间戳的文档**（规范/清单/手册）→ 用英文 kebab-case 或中文主题名，靠目录体现类型。
3. **禁用**：空格、全角字符、无意义序号（`新建文档.md`、`文档1.md`、`最终版.md`）。
4. **中文与英文混排**：主题用中文（可读性好），类型/日期用标准格式。

### 2.2 按类型细表

| 类型 | 格式 | 示例 |
|---|---|---|
| 审计报告 | `YYYYMMDD-<主题>-审计报告.md` | `20260831-看板缓存优化-审计报告.md` |
| 审查/审核报告 | `YYYYMMDD-<主题>-审查报告.md` | `20260820-全栈审查报告.md` |
| 调试归档 | `debug-<主题>-<YYYYMMDD>.md` | `debug-cs-record-gap-20260902.md` |
| 设计（未锁定） | `<主题>-<YYYYMMDD>.md` | `投诉数据接入设计.md` |
| 规范 | `<主题>规范.md` 或 kebab-case | `数据时间字段使用规范.md` |
| 运维手册 | `<主题>-运维手册.md` | `数据采集-运维手册.md` |
| 说明文档 | `<主题>说明.md` | `数据库表结构说明.md` |

---

## 三、存量归位映射（本次执行）

| 现状（根目录） | 目标位置 | 新文件名 |
|---|---|---|
| 审计报告-看板缓存优化-2026-08-31.md | `docs/reports/` | `20260831-看板缓存优化-审计报告.md` |
| 审计报告-部署日志分析-2026-08-30.md | `docs/reports/` | `20260830-部署日志分析-审计报告.md` |
| 审计报告-全栈审计-v0.7.md | `docs/reports/` | `20260902-全栈审计-审计报告.md` |
| 审核报告-全栈审查-v0.6.md | `docs/reports/` | `20260820-全栈审查-审查报告.md` |
| 审核报告-界面工作流管理效果.md | `docs/reports/` | `界面工作流管理效果-审查报告.md` |
| 审查报告-规划设计审查-v0.6.md | `docs/reports/` | `规划设计审查-审查报告.md` |
| 数据时间审核报告.md | `docs/reports/` | `20260801-数据时间审核-审计报告.md` |
| 数据采集运维手册.md | `docs/ops/` | `数据采集-运维手册.md` |
| 数据时间字段使用规范.md | `docs/standards/` | 保持原名 |
| 数据同步与加工说明.md | `docs/standards/` | 保持原名 |
| 数据源接入与缓存加速规范.md | `docs/standards/` | 保持原名 |
| workbuddy数据同步接入规范.md | `docs/standards/` | 保持原名 |
| workbuddy手动退款数据同步接入规范.md | `docs/standards/` | 保持原名 |
| 外部只读数据接入规范.md | `docs/standards/` | 保持原名 |
| 投诉数据接入说明.md | `docs/standards/` | 保持原名 |
| 数据库表结构说明.md | `docs/standards/` | 保持原名 |
| 人均效率算法说明.md | `docs/standards/` | 保持原名 |
| Group-Specialty双体系设计.md | `docs/design/` | 保持原名 |
| 腾讯云部署迁移方案.md | `docs/design/` | 保持原名 |
| 共享文档投诉数据接入设计.md | `docs/design/` | 保持原名 |
| 需求派工单-外部工具只读开放平台-草稿.md | `docs/design/` | 保持原名 |
| 审查行动项登记.md | `docs/`（总纲） | 保持原名 |
| alembic-migration管理规范.md | `docs/standards/` | 保持原名 |
| API接口文档.md | `docs/`（总纲） | 保持原名 |
| 规划设计.md / 规划设计-v0.5.md | `docs/`（总纲） | 保持原名 |

> 注意：`audit-report-20260820.html`、`csctalon.xmind` 为附件，保持原位不移动。

---

## 四、运维手册的分发（不在生产目录）

`docs/` 不随发布包进生产（`package.sh` 排除 `docs`，`upgrade.sh` 清理 `docs`），因此运维手册在**生产服务器上不可见**。运维人员获取方式二选一：

1. **首选**：从代码仓库（Gitea）在线查看——运维手册与代码同仓库，永远是最新版本。
2. **兜底**：将手册同步到团队共享文档/知识库，供无仓库访问权限的现场人员查阅。

> 原则：手册跟随代码仓库，不单独部署到运行环境；如确需服务器现场可查，可在 `.env` 指向的 `logs/` 同级放一份拷贝，但**不要**改发布打包清单。

---

## 五、存量迁移 SOP（未来再次归位时复用）

> 本次（2026-09-03）已完成首轮归位。若未来再次新增分类或调整命名，按此 SOP 执行，避免断链。

### 5.1 步骤

1. **先建工单**（governance 类），明确迁移映射表（旧路径 → 新路径）。
2. **盘点交叉引用**：`grep -rn '旧文件名' --include='*.md'` 找全引用方。
3. **先改引用，再移动**（或移动后立即同步改，顺序必须闭环）：
   - `docs/xxx.md` 绝对路径引用 → 直接替换为新路径；
   - `](xxx.md)` 相对路径引用 → 按「当前文件目录到目标文件目录」重算相对路径。
4. **用 `git mv` 移动**（保留历史，git 识别 rename）。
5. **验证断链**：遍历所有 `.md` 的相对链接，逐一检查目标文件是否存在。
6. **提交并关联工单号**，gitleaks 通过后推送。

### 5.2 断链验证脚本（可直接复用）

```bash
cd <repo_root>
python3 - <<'PY'
import os, re
broken = []
for root, dirs, fs in os.walk("."):
    if ".git" in root or "node_modules" in root or ".venv" in root:
        continue
    for f in fs:
        if not f.endswith(".md"):
            continue
        path = os.path.join(root, f)
        content = open(path, encoding="utf-8").read()
        for m in re.finditer(r'\]\(([^)]+)\)', content):
            t = m.group(1)
            if t.startswith(("http://", "https://", "file://", "#")):
                continue
            tc = t.split("#")[0]
            if not tc.endswith(".md"):
                continue
            tp = os.path.normpath(os.path.join(root, tc))
            if not os.path.exists(tp):
                broken.append((path, t))
if broken:
    print("BROKEN:")
    for p, t in broken:
        print("  ", p, "->", t)
else:
    print("NO BROKEN LINKS")
PY
```

### 5.3 易错点（本次踩坑）

- **相对路径 vs 绝对路径**：`docs/xxx.md` 开头的引用不受文件位置影响，可直接 sed 全局替换；`](xxx.md)` 相对引用必须按新位置重算 `../` 层级。
- **跨目录 `../` 数量**：文件从根目录移入 `docs/design/` 后，引用仓库根文件需从 `../AGENTS.md` 改为 `../../AGENTS.md`。
- **历史遗留链接**：指向已迁移目录（如 `.trae/documents/`）的链接，应改指向 arch 里对应的锁定文档。
- **README 目录树与索引**：移动后必须同步更新 README 的目录树代码块和文档索引表。

