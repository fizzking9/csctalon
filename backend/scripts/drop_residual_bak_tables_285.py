# -*- coding: utf-8 -*-
"""清理生产残留手工备份表 (#285, #274 审计 C1)

目标两表(不在任何模型中, 代码零引用):
- wallet_withdrawals_bak_20260919 (69,455 行 / 16MB)
- ai_reports_bak_20260920 (76 行)

## 执行前置(工单要求)
1. 两表已 pg_dump 导出至 /opt/csctalon/backups/(本脚本会检查 --dump-tag
   对应的导出文件存在且非空)。
2. 默认 dry-run; 真正执行必须同时显式提供 --apply 与 --allow-real-db
   (后者禁止由环境变量隐式推导, 见 db_guard 文档)。

## 安全设计
- bootstrap 固定 prod 模式并校验"模式↔库名"自洽;
- assert_safe_to_destroy 操作期二次守卫;
- DROP 前再次核对表名常量与行数, 防止表名漂移/误删。
"""
import sys
from pathlib import Path

from sqlalchemy import text

# bootstrap 必须先于任何 app 导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._bootstrap import bootstrap, real_db_requested  # noqa: E402

# 目标表白名单: 表名常量, DROP 只可能命中这两张
TARGETS = {
    "wallet_withdrawals_bak_20260919": 69455,
    "ai_reports_bak_20260920": 76,
}

_BACKUPS_DIR = Path("/opt/csctalon/backups")


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    real_db = real_db_requested()

    db = bootstrap(mode="prod", allow_real_db=real_db)
    try:
        assert_safe = None
        from app.core.db_guard import assert_safe_to_destroy
        assert_safe = assert_safe_to_destroy

        dbname = db.execute(text("SELECT current_database()")).scalar()
        print(f"目标库: {dbname}")

        # ── 1. 核对表存在与行数 ──
        verified: list[str] = []
        for tbl, expect in TARGETS.items():
            exists = db.execute(text(
                "SELECT to_regclass(:name) IS NOT NULL"
            ), {"name": f"public.{tbl}"}).scalar()
            if not exists:
                print(f"  跳过 {tbl}: 表不存在(可能已清理)")
                continue
            n = db.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
            # 行数允许小幅偏差(备份后可能有新写入), 偏差 >5% 则中止防意外
            if abs(n - expect) / expect > 0.05:
                print(f"✘ {tbl} 实际行数 {n} 与预期 {expect} 偏差 >5%, 中止")
                return 3
            print(f"  核实 {tbl}: {n} 行(预期 {expect})")
            verified.append(tbl)

        if not verified:
            print("✔ 无残留表, 无需操作")
            return 0

        # ── 2. 检查 pg_dump 导出文件存在且非空 ──
        tag = None
        for i, a in enumerate(sys.argv[1:]):
            if a == "--dump-tag":
                tag = sys.argv[i + 2]
        if not tag:
            print("✘ 缺少 --dump-tag <日期标签>, 请先完成 pg_dump 导出")
            return 4
        for tbl in verified:
            f = _BACKUPS_DIR / f"{tbl}_{tag}.sql"
            if not f.exists() or f.stat().st_size == 0:
                print(f"✘ 导出文件不存在或为空: {f}")
                return 5
            print(f"  导出归档: {f} ({f.stat().st_size // 1024} KB)")

        # ── 3. 执行 DROP ──
        if not apply or not real_db:
            print("\n【DRY-RUN】将 DROP 以上表; 真正执行请同时加 --apply --allow-real-db")
            return 0

        assert_safe(db, reason="删除生产残留 bak 表 #285", allow_real_db=True)
        for tbl in verified:
            # 表名来自上方白名单常量, 非外部输入, 无需参数化标识符
            db.execute(text(f'DROP TABLE IF EXISTS "{tbl}"'))
            print(f"  已 DROP {tbl}")
        db.commit()
        print("✔ 清理完成")
        return 0
    except Exception as e:
        db.rollback()
        print(f"✘ 失败: {type(e).__name__}: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
