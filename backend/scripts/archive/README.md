# scripts/archive/ — 历史调试/探针/测试脚本归档

> **性质**：参考（REF）—— 历史脚本归档说明
> **创建**：2026-10-08（工单 #231 / B1-6 / R-P1-12）

## 归档范围

本目录存放**一次性调试 / 探针 / 测试脚本**，特征：
- 文件名以 `_debug_` / `_test_` / `debug_` / `probe_` / `test_` / `sniff_` / `find_` 开头
- 或为登录辅助（`kdocs_login` / `tencent_doc_login`）
- **绕过** `scripts/_bootstrap.py` 的运行模式守卫与 db_guard
- 一次性任务现场，非生产路径（AGENTS.md 仓库入库规范 C5：临时脚本不进 git）

## 为什么不直接删

- 部分脚本含**调试经验**（如 SCRM 登录流程的探针步骤），后续若同类问题复发可参考
- 直接删会让 git history 检索变难（`git log --all` 才能找回）
- 归档到子目录 + 显式 README，既不在顶层 `scripts/` 增加噪音，也保留可追溯性

## 为什么不在顶层 `scripts/`

AGENTS.md 绝对禁止第 14 条 / R-P1-12 要求：顶层 `scripts/` 仅保留**合规脚本**
（即调用 `bootstrap()` 进入，过 db_guard 守卫的脚本）。本目录的脚本不符合该要求。

## 如何使用归档脚本

**默认不要运行**。若确需参考：
1. 先读懂 `scripts/_bootstrap.py` 的运行模式守卫与 db_guard 机制
2. 复制到 `workdata/`（gitignore）按需改造，加 `bootstrap()` 调用
3. 不要在顶层 `scripts/` 直接放回

## 归档清单（31 个脚本，2026-10-08）

### `_debug_*` 调试脚本（6）
- `_debug_aggressive.py`
- `_debug_cap3.py`
- `_debug_ticket_export_500.py`
- `_debug_ticket_export_full.py`
- `_debug_ticket_export_simple.py`
- `_debug_ticket_upload_records.py`
- `_debug_ticket_upsert_bug.py`

### `_test_*` 测试脚本（3）
- `_test_ticket_upsert_fix.py`
- `_test_workbuddy_push.py`

### `_fix_captcha_*` 一次性修复（2）
- `_fix_captcha_type_math.py`
- `_fix_captcha_type_sql.py`

### `_offline_test_*` 离线测试（1）
- `_offline_test_math_captcha.py`

### `debug_*` 调试脚本（5）
- `debug_baiyin_manual.py`
- `debug_scrm_captcha_detail.py`
- `debug_scrm_cols.py`
- `debug_scrm_login.py`
- `debug_scrm_opts.py`

### `probe_*` 探针脚本（7）
- `probe_baiyin_menus.py`
- `probe_baiyin_submenus.py`
- `probe_baiyin_user_urls.py`
- `probe_baiyinqifu.py`
- `probe_scrm_after_login.py`
- `probe_scrm_login.py`

### `test_*` 测试脚本（4）
- `test_baiyinqifu_captcha.py`
- `test_scrm_dual_mode.py`
- `test_scrm_routes.py`
- `test_scrm_text_captcha.py`

### `sniff_*` / `find_*` 探针（2）
- `sniff_shanmao_export_request.py`
- `find_scrm_admin_domains.py`

### 登录辅助（2）
- `kdocs_login.py`
- `tencent_doc_login.py`

## 后续

顶层 `scripts/` 剩余的**运营脚本**（`init_*` / `cleanup_*` / `fix_*` / `apply_*` / `import_*` / `mark_*` / `verify_*` / `export_*` / `run_*` / `check_*` 等）仍**绕过 bootstrap**，
其迁移到 `bootstrap()` 调用的工作量大，作为后续技术债（建议归入 B2 或择机项 O-4）。
本工单（#231）仅做归档收口，不强制迁移运营脚本。
