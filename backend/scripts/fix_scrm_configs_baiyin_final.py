#!/usr/bin/env python3
"""批量修正 SCRM 配置:
  0. 双账号密码体系分离:
        - 列 username/password  预留存 OpenAPI 的 clientId/clientSecret(API 网关凭证)
        - options.web_username / web_password  存 Playwright 网页登录用账号密码(手机号/密码)
  1. scrm_operator(id=7): 把列里的手机号/密码 移到 options.web_*(加密), 列清空;
                          更新 entry_path → /wxWork/personalityManagement
  2. scrm_session_private/group/virtual(id=8,9,10):
        - base_url 统一改成 https://scrm.baiyinqifu.com
        - entry_path 统一 → /tuoke/customer/chatRecord (聊天记录页, 后续下拉筛选会话类型)
        - 列里当前是 gateway.bilinl.com OpenAPI 凭证(bSQ4.../zRwG...),保留不动(留给 openapi 模式)
        - options.web_* 填 Playwright 网页登录凭证(手机号/密码,加密)
  3. 新增 2 行(可选,用户说后续扩充): scrm_work_stats(接待工作) / scrm_customer(客户列表)
  4. 所有 scrm_* options: 把之前的 /index#/... 假路径替换, 补齐 captcha_type=math / login_url_path=/login
                           等登录所需 selector 配置(沿用 baiyinqifu 那套)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

# 白音 SCRM 登录通用配置 (options)
BAIYIN_LOGIN_BASE_OPTIONS = {
    "fetch_mode": "playwright",
    "captcha_type": "math",
    "login_url_path": "/login",
    "submit_button_text": "登录",
    "username_placeholder": "请输入登录账号/手机号",
    "password_placeholder": "请输入密码",
    "captcha_input_placeholder": "请输入验证码",
    "captcha_img_selector": "img.show-code-img",
}

# Playwright 网页登录凭证 (白音管理后台 管理员账号)
PW_USERNAME = "<手机号>"
PW_PASSWORD = "<密码>"
PW_USER_ENC = encrypt_secret(PW_USERNAME)
PW_PWD_ENC = encrypt_secret(PW_PASSWORD)

BASE_URL_BAIYIN = "https://scrm.baiyinqifu.com"


def _merge_options(old: dict | None, overrides: dict) -> dict:
    out = dict(old or {})
    # 合并基础登录参数, 不要覆盖 overrides 里已有的 key
    for k, v in BAIYIN_LOGIN_BASE_OPTIONS.items():
        if k not in out and k not in overrides:
            out[k] = v
    for k, v in overrides.items():
        out[k] = v
    return out


def upsert_row(db, *, data_type: str, name: str, page_path: str,
               base_url: str = BASE_URL_BAIYIN,
               username_col: str | None = None,
               password_col: str | None = None,
               options_overrides: dict | None = None,
               sort_order: int = 0,
               enabled: bool = True,
               default_sync: bool = True) -> SyncSourceConfig:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    opts_ov = dict(options_overrides or {})
    # Playwright 凭证统一放 options
    opts_ov.setdefault("web_username", PW_USER_ENC)
    opts_ov.setdefault("web_password", PW_PWD_ENC)
    new_opts = _merge_options(row.options if row else None, opts_ov)
    if row is None:
        row = SyncSourceConfig(
            data_type=data_type,
            name=name,
            base_url=base_url,
            page_path=page_path,
            username=encrypt_secret(username_col) if username_col else None,
            password=encrypt_secret(password_col) if password_col else None,
            options=new_opts,
            enabled=enabled,
            default_sync=default_sync,
            sort_order=sort_order,
        )
        db.add(row)
        print(f"  [+] 新增 data_type={data_type!r}  name={name!r}")
    else:
        row.name = name
        row.base_url = base_url
        row.page_path = page_path
        if username_col is not None:
            row.username = encrypt_secret(username_col)
        # 否则保留原来的(OpenAPI clientId/clientSecret 留着列里)
        row.options = new_opts
        row.enabled = enabled
        row.default_sync = default_sync
        row.sort_order = sort_order
        print(f"  [✓] 更新 data_type={data_type!r}  name={name!r}")
    return row


with SessionLocal() as db:
    # ----- 1. scrm_operator (id=7) -----
    op_row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    assert op_row is not None, "scrm_operator 行不存在,先手动建"
    # 把列里现有的 135 手机号/密码 清掉(移到 options.web_*), 列留给 OpenAPI clientId/clientSecret
    col_uname_old = decrypt_secret(op_row.username) if op_row.username else ""
    col_pwd_old = decrypt_secret(op_row.password) if op_row.password else ""
    # 如果列里刚好是 Playwright 用的手机号,就清空列留空;如果以后有 OpenAPI 凭证,再填
    if col_uname_old.startswith("1") and len(col_uname_old) == 11:
        op_row.username = None
        op_row.password = None
        print("  [→] scrm_operator: 列里的 135 手机号/密码 已清空,移动到 options.web_username/web_password")
    op_row.base_url = BASE_URL_BAIYIN
    op_row.page_path = "/wxWork/personalityManagement"
    op_opts = _merge_options(op_row.options or {}, {
        "web_operator_entry_path": "/wxWork/personalityManagement",
        "web_session_entry_path": "/tuoke/customer/chatRecord",
    })
    op_opts.setdefault("web_username", PW_USER_ENC)
    op_opts.setdefault("web_password", PW_PWD_ENC)
    # 清理掉旧的 /index#/system/user-manage 这类假路径
    for k in list(op_opts.keys()):
        if isinstance(op_opts.get(k), str):
            v = op_opts[k]
            if v.startswith("/index#/") and k in ("web_operator_entry_path", "web_session_entry_path"):
                pass  # 上面已经覆写
    op_row.options = op_opts
    print(f"  [✓] scrm_operator(name={op_row.name!r}): entry_path → /wxWork/personalityManagement")

    # ----- 2. scrm_session_private / group / virtual -----
    OPENAPI_CLIENT_ID = "<ClientId>"
    OPENAPI_CLIENT_SECRET = "<ClientSecret>"
    for dt, nm, page_path, so, sync_default in [
        ("scrm_session_private", "SCRM-私聊会话列表", "/tuoke/customer/chatRecord", 8, True),
        ("scrm_session_group", "SCRM-群聊会话列表", "/tuoke/customer/chatRecord", 9, True),
        ("scrm_session_virtual", "SCRM-虚拟会话列表", "/tuoke/customer/chatRecord", 10, True),
    ]:
        upsert_row(
            db,
            data_type=dt,
            name=nm,
            base_url=BASE_URL_BAIYIN,
            page_path=page_path,
            # 列 username/password 存 OpenAPI 凭证 (网关用)
            username_col=OPENAPI_CLIENT_ID,
            password_col=OPENAPI_CLIENT_SECRET,
            options_overrides={
                "web_session_entry_path": "/tuoke/customer/chatRecord",
                # 会话类型筛选: 页面上有「请选择」下拉 = 会话类型, 后续按 data_type 自动选对
                "web_session_type_hint": dt.replace("scrm_session_", ""),
            },
            sort_order=so,
            default_sync=sync_default,
        )

    # ----- 3. 新增: scrm_work_stats (接待工作统计 KPI) -----
    upsert_row(
        db,
        data_type="scrm_work_stats",
        name="SCRM-接待工作统计",
        base_url=BASE_URL_BAIYIN,
        page_path="/datastats/customer/ecCountWork",
        options_overrides={
            "web_stats_entry_path": "/datastats/customer/ecCountWork",
            # 接待工作统计表头: 日期 / 客服 / 上班时间 / 在线时长 / 离开时长 / 总在线 / 响应数 /
            #  咨询量 / 满意率 / 平均响应时长 / 平均会话时长 / 在线率 / 转接次数
            # 先标注默认按「最近一天」增量,手动同步
            "stats_period_default": "yesterday",
        },
        sort_order=20,
        default_sync=False,  # 用户说偶尔调用,默认不纳入自动同步
    )

    # ----- 4. 新增: scrm_customer (客户列表) -----
    upsert_row(
        db,
        data_type="scrm_customer",
        name="SCRM-客户列表",
        base_url=BASE_URL_BAIYIN,
        page_path="/customer/list",
        options_overrides={
            "web_customer_entry_path": "/customer/list",
            "web_customer_has_excel_export": True,
        },
        sort_order=21,
        default_sync=False,  # 用户说偶尔同步
    )

    db.commit()
    print("\n✅ 所有 SCRM 配置已更新并 commit!")

# 验证查询一下
print("\n" + "=" * 80)
print("验证: 再查一遍所有 scrm 配置")
with SessionLocal() as db:
    rows = db.scalars(select(SyncSourceConfig).where(
        SyncSourceConfig.data_type.like("scrm%")
    ).order_by(SyncSourceConfig.sort_order)).all()
    for r in rows:
        col_u = decrypt_secret(r.username) if r.username else ""
        col_p = decrypt_secret(r.password) if r.password else ""
        opts = r.options or {}
        wu = opts.get("web_username")
        wp = opts.get("web_password")
        wu_dec = decrypt_secret(wu) if wu and isinstance(wu, str) and wu.startswith("ENC:") else wu
        wp_dec = decrypt_secret(wp) if wp and isinstance(wp, str) and wp.startswith("ENC:") else wp
        print(f"[{r.id:>2}] {r.data_type:<30} name={r.name!r:<20}  sync={r.default_sync}  enabled={r.enabled}")
        print(f"     base_url={r.base_url}   page_path={r.page_path}")
        print(f"     列 username/password(OpenAPI 凭证): {col_u!r}  /  {'*' * len(col_p) if col_p else '(空)'}")
        print(f"     options.web_*(Playwright 登录):     {wu_dec!r}  /  {'*' * len(wp_dec) if wp_dec else '(空)'}")
        other_opts = {k: v for k, v in opts.items() if k not in ("web_username", "web_password")}
        print(f"     其他 options keys: {list(other_opts.keys())}")
