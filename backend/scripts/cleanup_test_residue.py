"""清理 csctalon_test 里 `test_wide_table_builder` 遗留的合成数据

## 背景 (2026-09-22)

`_build_wide_batch` 原先内部 `db.commit()`, 绕过 pytest fixture 的事务回滚,
每次跑测试都往测试库留下几行 `SN-WIDE-*` / `GDC-WIDE-*` 合成数据(累计 193 行)。

这些行 `service_category='投诉'` 但 `register_time` 为 NULL →
A2/A5 相关测试的筛选条件恒为 0 条 → 35 个断言被跳过、从未真正执行。

服务端已修(`commit=False` 开关), 本脚本清理**历史残留**。

## 安全措施

配 pytest 同级的守卫: 库名必须含 `test`, 否则拒绝执行。
⚠️ 依据 conftest.py 记录的 #42 数据事故 —— 曾有测试连到开发库
   并用无条件 DELETE 清空业务表(refunds 170960→2)。本脚本沿用同一守卫。

## 幂等

可重复执行: 无匹配行时报告 0 并正常退出。
"""
import os
import sys

# ── 库名守卫（必须在 import app 之前设置，且必须在删除前校验）──
os.environ["POSTGRES_DB"] = os.environ.get("TEST_POSTGRES_DB", "csctalon_test")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

# 合成数据的可识别标记。
#
# ⚠️ 用「前缀清单」不可靠 —— 测试里的合成前缀有 20+ 种
#    (SN-WIDE/SN-EC/SN-SCOPE/SN-DUP/SN-NONE/SNTEST...), 且会随新测试增加。
#    故改用**特征判定**: 测试造的 SN 一律是 `SN-<大写标签>-<8位hex>` 形态
#    (见各测试的 `_uniq()` / `f"SN-{{...}}"` 写法), 与生产 SN 形态不同。
#
# 生产 SN 特征(实测): 形如 `867514032345678` 等纯数字/IMEI 类, 不含 `SN-` 前缀。
_SN_PREFIX_RE = r"^SN[-A-Z0-9]*[A-Z]-[0-9a-f]{6,8}$|^SN-?[A-Z]+[0-9]*$"
_TICKET_PREFIX_RE = r"^GDC-(WIDE|TEST)-|^GDC-[0-9a-f]{6}$"


def main(apply: bool = False) -> int:
    db = SessionLocal()
    try:
        # ── 守卫 1: 库名必须是测试库 ──
        dbname = db.execute(text("SELECT current_database()")).scalar()
        if "test" not in (dbname or "").lower():
            print(f"✘ 拒绝执行: 当前库 {dbname!r} 不含 'test' —— "
                  f"本脚本只允许在测试库上运行")
            return 2
        print(f"目标库: {dbname}")

        # ── 守卫 2: 待删行必须**全部**是合成数据（不允许有真实数据混入）──
        #
        # ⚠️ SN 必须从**多表联合**取 —— 只查 service_tickets 会漏:
        #    实测清理完 service_tickets 后, refund_wide/devices 仍留着 214 行,
        #    因为它们记录的 SN 在 service_tickets 里已不存在(先前的部分清理所致)。
        #    用 UNION 汇总所有可能持有 SN 的表, 才能清干净。
        sns = [r[0] for r in db.execute(text("""
            SELECT DISTINCT sn FROM devices WHERE sn ~ :re
            UNION
            SELECT DISTINCT device_sn FROM service_tickets WHERE device_sn ~ :re
            UNION
            SELECT DISTINCT device_sn FROM refunds WHERE device_sn ~ :re
            UNION
            SELECT DISTINCT device_sn FROM refund_wide WHERE device_sn ~ :re
            UNION
            SELECT DISTINCT device_sn FROM wallet_withdrawals WHERE device_sn ~ :re
        """), {"re": _SN_PREFIX_RE}).all() if r[0]]

        total_tickets = db.execute(text(
            "SELECT count(*) FROM service_tickets")).scalar()
        if sns:
            n_real = db.execute(text(
                "SELECT count(*) FROM service_tickets "
                "WHERE register_time IS NOT NULL AND device_sn = ANY(:sns)"
            ), {"sns": sns}).scalar()
            if n_real:
                print(f"✘ 拒绝执行: 待删的 {len(sns)} 个 SN 中有 {n_real} 行 "
                      f"register_time 非空（疑似真实数据）")
                return 3

        print(f"待清理 SN: {len(sns)} 个（均为合成数据, register_time 全为 NULL）")
        if not sns:
            print("✔ 无残留，无需清理")
            return 0

        # ── 按外键顺序删除（先子表后父表）──
        plan = [
            ("refund_wide",
             "DELETE FROM refund_wide WHERE device_sn = ANY(:sns)"),
            ("refunds",
             "DELETE FROM refunds WHERE device_sn = ANY(:sns)"),
            ("wallet_withdrawals",
             "DELETE FROM wallet_withdrawals WHERE device_sn = ANY(:sns)"),
            ("service_tickets",
             "DELETE FROM service_tickets WHERE device_sn = ANY(:sns)"),
            ("external_complaints",
             "DELETE FROM external_complaints WHERE device_sn = ANY(:sns)"),
            ("data_match_relations",
             "DELETE FROM data_match_relations "
             "WHERE dst_id = ANY(:sns) OR src_id = ANY(:sns)"),
            ("devices", "DELETE FROM devices WHERE sn = ANY(:sns)"),
        ]

        print()
        if not apply:
            print("【DRY-RUN】将删除以下行（加 --apply 才真正执行）:")
        for tbl, sql in plan:
            try:
                n = db.execute(text(
                    sql.replace("DELETE FROM", "SELECT count(*) FROM", 1)
                ), {"sns": sns}).scalar()
            except Exception as e:
                print(f"  跳过 {tbl:24s} ({type(e).__name__})")
                continue
            if apply:
                db.execute(text(sql), {"sns": sns})
                print(f"  已删除 {tbl:24s} {n:>5d} 行")
            else:
                print(f"  将删除 {tbl:24s} {n:>5d} 行")

        if apply:
            db.commit()
            remain = db.execute(text("SELECT count(*) FROM service_tickets")).scalar()
            print()
            print(f"✔ 清理完成: service_tickets {total_tickets} → {remain} 行")
        else:
            print()
            print(f"（当前 service_tickets 共 {total_tickets} 行）")
        return 0
    except Exception as e:
        db.rollback()
        print(f"✘ 失败: {type(e).__name__}: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
