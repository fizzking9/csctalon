#!/usr/bin/env python3
"""共享文档批量导入 POC —— 读取层 → 复用 complaint_sync 管线真实入库

验证目标(对应《共享文档数据同步系统建设方案》快速验证框架):
  1. 读取层产出「中文表头 dict 行」(见 _poc_shared_doc_reader.py);
  2. 把 315/卡商 两个 Sheet 组装成 payload;
  3. 调 sync_complaints_from_payload → 清洗/指纹去重/SN关联工单/入库;
  4. 打印 complaint_sync_logs 记录 id + 统计(315/卡商 新增/更新/失败/关联)。

⚠️ 本脚本会对本地 dev 库真实写库(与 315投诉.xlsx 指纹去重, 重复跑不会产生
脏数据, 只会更新)。建议在 dev 环境跑。

用法:
  cd backend && ./.venv/bin/python ../scripts/_poc_shared_doc_sync.py \
      --file ../01样本数据/315投诉.xlsx \
      --ext-sheet "监督局-315投诉" --card-sheet "卡商群投诉" \
      --source-file "315投诉.xlsx" --sync-source "poc_shared_doc"

  # 不落库模式(只验证 读取层→payload→列名映射, 无需本地库 schema 最新):
  cd backend && ./.venv/bin/python ../scripts/_poc_shared_doc_sync.py \
      --file ../01样本数据/315投诉.xlsx --ext-sheet "监督局-315投诉" \
      --card-sheet "卡商群投诉" --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.core.database import SessionLocal
from app.services.complaint_sync import sync_complaints_from_payload
from app.services.data_parser import COLUMN_TEMPLATES, suggest_mapping

# 复用读取层(同一目录)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _poc_shared_doc_reader import read_sheet_to_records  # noqa: E402


def _COLUMN_TEMPLATE_FIELDS(data_type: str) -> set[str]:
    """取某数据类型模板的目标字段集(complaint_sync 能识别的字段)"""
    return set(COLUMN_TEMPLATES.get(data_type, {}).keys())


def main() -> int:
    ap = argparse.ArgumentParser(description="共享文档批量导入 POC(真实入库 / --dry-run 不落库)")
    ap.add_argument("--file", required=True, help="xlsx 路径")
    ap.add_argument("--ext-sheet", required=True, help="315 外部投诉 Sheet 名")
    ap.add_argument("--card-sheet", default=None, help="卡商群投诉 Sheet 名(可省略)")
    ap.add_argument("--source-file", default="poc-shared-doc", help="sync_log 里的来源文件名")
    ap.add_argument("--sync-source", default="poc_shared_doc", help="sync_source 标记")
    ap.add_argument("--dry-run", action="store_true", help="不落库: 只验证 读取→payload→列名映射")
    args = ap.parse_args()

    f = Path(args.file)
    if not f.exists():
        print(f"❌ 文件不存在: {f}")
        return 2

    # ── 1) 读取层: Sheet → 中文表头 dict 行(平台无关) ──
    print(f"📖 读取 [{f.name}]")
    ext_records = read_sheet_to_records(f, sheet_name=args.ext_sheet)
    print(f"  · 315 Sheet [{args.ext_sheet}] → {len(ext_records)} 行")
    card_records = []
    if args.card_sheet:
        card_records = read_sheet_to_records(f, sheet_name=args.card_sheet)
        print(f"  · 卡商 Sheet [{args.card_sheet}] → {len(card_records)} 行")

    if not ext_records and not card_records:
        print("❌ 两个 Sheet 都没有数据行, 中止")
        return 3

    # ── 2) 组装 payload(与 workbuddy 推送/腾讯文档同步同一结构) ──
    payload = {
        "source_file": args.source_file,
        "external_complaints": ext_records,
        "card_complaints": card_records,
    }

    # ── 2.5) dry-run: 不落库, 用 suggest_mapping 校验列名能否映射到目标字段 ──
    if args.dry_run:
        print("\n🧪 dry-run: 校验读取层列头 → complaint_sync 模板映射(不写库)")
        for label, records, data_type in (
            ("315 外部投诉", ext_records, "external_complaint"),
            ("卡商群投诉", card_records, "card_complaint"),
        ):
            if not records:
                continue
            cols = list(records[0].keys())
            mapping = suggest_mapping(data_type, cols)
            template = _COLUMN_TEMPLATE_FIELDS(data_type)
            mapped = {v for v in mapping.values()}
            missed = [c for c in cols if c not in mapped]
            hit = sum(1 for k in mapping if k in template)
            print(f"\n  [{label}] 列头({len(cols)}列) → 映射命中 {hit}/{len(template)} 个模板字段")
            print(f"    映射: { {k: v for k, v in mapping.items() if k in template} }")
            if missed:
                print(f"    ⚠ 未参与映射的列(可能被管线忽略, 供核对): {missed}")
        print("\n✅ dry-run 完成: 读取层 → payload 契约成立(真实入库需本地库 schema 到 head)")
        return 0

    # ── 3) 复用统一管线入库(清洗/指纹去重/SN关联/写 sync_log/缓存失效) ──
    db = SessionLocal()
    try:
        stats = sync_complaints_from_payload(
            db,
            payload,
            source_file=args.source_file,
            sync_source=args.sync_source,
        )
    finally:
        db.close()

    # ── 4) 打印结果 ──
    print("\n✅ 入库完成. sync_log id =", stats.get("log_id"), " status =", stats.get("status"))
    for k in ("external", "card"):
        s = stats[k]
        print(
            f"  · {k:8s}: total={s['total']:5d}  +新增={s['inserted']:4d}  "
            f"~更新={s['updated']:4d}  !失败={s['failed']:3d}  "
            f"过滤空行={s['filtered']:3d}  关联工单={s['matched']:4d}"
        )
    errs = stats["external"].get("errors") or stats["card"].get("errors")
    if errs:
        print("\n⚠ 部分行警告/错误(前 5 条):")
        for e in errs[:5]:
            print("   ", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
