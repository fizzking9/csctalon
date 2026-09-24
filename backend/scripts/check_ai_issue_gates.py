# -*- coding: utf-8 -*-
"""AI 报告相关工单的「到期核验」脚本(只读) —— #191 / #193。

## 为什么存在

#191 与 #193 的关闭条件需要"时间窗"证据(连续 2 日自动采集 / 7 天观测表), 到点才可核验。
把核验做成**一条命令**, 避免"到点凭记忆手敲 SQL"或"凭证不可复现"。

## 用法(只读, 但连真实库仍需显式授权)

    # 在 backend 容器内(生产):
    docker exec -w /app -e PYTHONPATH=/app csctalon-backend \
      python /app/scripts/check_ai_issue_gates.py --issue 191 --allow-real-db

    # 本地(默认 dev 库; 核生产需加 --allow-real-db 且环境可达):
    backend/.venv/bin/python backend/scripts/check_ai_issue_gates.py --issue 193 --allow-real-db

退出码: 0=核验通过 · 1=未通过(打印差在哪) · 2=参数错误

## 判据出处(与工单正文逐条对应, 不另立口径)

- **#191**: ① 连续 2 个自然日 `scheduled` 档 `refund_record_report.total_rows > 0`;
  ② 数据真的推进: `check_freshness` 的 E 路 `lag_days <= 1` 且 `stale = false`。
  (仅 ①不够 —— 近 3 天窗口重复导出旧数据也会 >0, 见 #191 审计评论)
- **#193**: 观测类, 不做"通过/未通过"判定, 只出表: 每模板生成/命中(hit_mode 口径)/
  `matched_count` 交叉校验/源表 30 分钟零新增桶占比/跳过事件是否已存在(P1 是否实施)。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts._bootstrap import bootstrap, real_db_requested  # noqa: E402

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))


def _q(db, sql: str, **params):
    from sqlalchemy import text

    return db.execute(text(sql), params).all()


# ─────────────────────────── #191 ───────────────────────────


def check_191(db, *, days: int = 5) -> tuple[bool, list[str]]:
    """核验 #191 关闭条件 ①②; 返回 (是否通过, 打印行)。"""
    out: list[str] = []
    ok = True

    out.append("① 近 %d 天 E 路定时采集记录(bms_sync_logs, data_type=refund_record_report)" % days)
    rows = _q(db, """
        SELECT to_char(created_at + interval '8 hours','MM-DD HH24:MI') bj,
               trigger_type, status, total_rows
        FROM bms_sync_logs
        WHERE data_type = 'refund_record_report'
          AND created_at > now() - (:days || ' day')::interval
        ORDER BY id
    """, days=days)
    if not rows:
        out.append("   (无记录)")
    for bj, tt, st, n in rows:
        out.append(f"   {bj}  trigger={tt:<10} status={st:<8} total_rows={n}")
    sched_ok = [r for r in rows if r[1] == "scheduled" and (r[3] or 0) > 0]
    sched_days = sorted({r[0][:5] for r in sched_ok})
    out.append(f"   → 有数据的 scheduled 档: {len(sched_ok)} 条, 覆盖日期 {sched_days}")
    if len(sched_days) < 2:
        ok = False
        out.append("   ✗ 未满足「连续 2 个自然日 scheduled total_rows>0」"
                   "(E 路档位 15:00, 首次自动采集为部署次日 15:00)")

    out.append("② 数据是否真的推进(check_freshness, E 路; 阈值=max_lag_days)")
    from app.services.source_freshness_service import check_freshness

    for r in check_freshness(db):
        out.append("   " + str(r))
        if r["source"] == "refund_record_report":
            if r["stale"] or (r["lag_days"] is None) or r["lag_days"] > r["max_lag_days"]:
                ok = False
                out.append("   ✗ 最新业务日未推进到基准日前一天(lag/stale 超阈值)")
            else:
                out.append(f"   ✓ 最新业务日={r['latest']} 基准日={r['baseline']} "
                           f"lag={r['lag_days']} ≤ 阈值 {r['max_lag_days']}, 未超期")
    return ok, out


# ─────────────────────────── #193 ───────────────────────────


def check_193(db, *, days: int = 7) -> tuple[bool, list[str]]:
    """输出 #193 观测表(观测类, 不做通过/未通过判定; 数据齐即 True)。"""
    out: list[str] = []
    out.append("① 按模板: 生成次数 / 命中交付次数(hit_mode) / matched_count 交叉校验(近 %d 天)" % days)
    rows = _q(db, """
        WITH h AS (
          SELECT r.template_id, count(*) AS hit_cnt
          FROM ai_reports r
          WHERE r.is_public = FALSE AND r.hit_mode IN ('full','section')
            AND r.created_at > now() - (:days || ' day')::interval
          GROUP BY 1
        )
        SELECT t.code, count(*) gen_cnt,
               coalesce(max(h.hit_cnt), 0) hit_cnt,
               sum(CASE WHEN r.matched_count > 0 THEN 1 ELSE 0 END) matched_reports,
               round(avg(length(r.answer_md))) avg_len
        FROM ai_reports r JOIN ai_report_templates t ON t.id = r.template_id
        LEFT JOIN h ON h.template_id = r.template_id
        WHERE r.is_public = TRUE AND r.created_at > now() - (:days || ' day')::interval
        GROUP BY t.code ORDER BY gen_cnt DESC
    """, days=days)
    out.append(f"   {'模板':<24}{'生成':>6}{'命中交付':>9}{'matched>0':>11}{'均字数':>8}")
    for code, gen, hit, matched, avg_len in rows:
        out.append(f"   {code:<24}{gen:>6}{hit:>9}{matched:>11}{avg_len:>8}")
    out.append("   注: 命中交付以 hit_mode 为准(L2 已补记账); 两者趋同说明记账口径已一致")

    out.append("② 源表 30 分钟桶「零新增」占比(近 %d 天, 08:00~23:59; 判断刷新是否带来新数据)" % days)
    for table, col, label in (("refunds", "created_at", "退费单"),
                              ("service_tickets", "register_time", "投诉/服务工单登记")):
        try:
            r = _q(db, f"""
                WITH b AS (
                  SELECT date_trunc('hour', {col} AT TIME ZONE 'Asia/Shanghai')
                         + CASE WHEN extract(minute FROM {col} AT TIME ZONE 'Asia/Shanghai') >= 30
                                THEN interval '30 min' ELSE interval '0' END AS bucket
                  FROM {table}
                  WHERE {col} >= now() - (:days || ' day')::interval
                    AND extract(hour FROM {col} AT TIME ZONE 'Asia/Shanghai') BETWEEN 8 AND 23
                  GROUP BY 1
                )
                SELECT count(*), count(*) FILTER (WHERE b.bucket IS NULL)
                FROM generate_series(
                       date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')
                         - (:days || ' day')::interval + interval '8 hour',
                       date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '23 hour',
                       interval '30 min') s(bucket)
                LEFT JOIN b ON b.bucket = s.bucket
            """, days=days)
            tot, empty = r[0]
            pct = round(empty / tot * 100, 1) if tot else 0.0
            out.append(f"   {label:<14} 桶 {tot} 个 | 零新增 {empty} 个({pct}%)")
        except Exception as e:  # noqa: BLE001  源表口径变更时不阻塞
            out.append(f"   {label}: 查询失败({e}) —— 请核对时间列/索引(见 #193 实现方案 §五)")

    out.append("③ P1 是否已实施(是否存在跳过留痕事件)")
    n = _q(db, "SELECT count(*) FROM system_events WHERE action = 'pregen_skipped'")[0][0]
    if n:
        out.append(f"   ✓ 已有 {n} 条 pregen_skipped → 可按实现方案的分支 A 验收(含抽检)")
    else:
        out.append("   ✗ 无 pregen_skipped → P1 未实施, 10-01 需按**分支 B(人工观测)**降级验收, "
                   "并在凭证里显式写明")
    return True, out


CHECKS = {191: check_191, 193: check_193}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 报告相关工单的到期核验(只读)")
    ap.add_argument("--issue", type=int, required=True, choices=sorted(CHECKS),
                    help="要核验的工单号")
    ap.add_argument("--days", type=int, default=0, help="回溯天数(默认: #191=5, #193=7)")
    ap.add_argument("--allow-real-db", action="store_true",
                    help="显式允许连真实库(bootstrap 护栏要求)")
    ap.add_argument("--mode", choices=["dev", "test", "prod"], default=None,
                    help="运行模式(默认读 APP_MODE 环境变量; ⚠️ 不要写死 prod —— "
                         "本地误跑会报 prod 却连到同名本地库, 凭证失去意义)")
    args = ap.parse_args(argv)

    # ⚠️ 不写死 mode: 容器内 APP_MODE=prod 自然得到 prod, 本地为 dev。
    #    写死 prod 的后果(实测踩到): 本地运行会把库名默认成 `csctalon`(真实库名),
    #    输出去显示"模式=prod 库=csctalon", 但连的其实是本地同名库 → 凭证自欺。
    db = bootstrap(mode=args.mode, allow_real_db=real_db_requested())
    try:
        import os

        from app.core.config import settings

        # 打印**实际连接目标**(host/port/db/mode), 让凭证自带"它核查的是哪个库"的证据
        print(f"模式={os.environ.get('APP_MODE')} 库={os.environ.get('POSTGRES_DB')} "
              f"host={getattr(settings, 'POSTGRES_HOST', '?')}:{getattr(settings, 'POSTGRES_PORT', '?')} "
              f"工单=#{args.issue} 时间={_dt.datetime.now(_TZ_CN).isoformat(timespec='seconds')}")
        print("-" * 78)
        days = args.days or (5 if args.issue == 191 else 7)
        ok, lines = CHECKS[args.issue](db, days=days)
        print("\n".join(lines))
        print("-" * 78)
        print("核验结论:", "通过 ✅" if ok else "未通过 ❌(见上面 ✗ 项)")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
