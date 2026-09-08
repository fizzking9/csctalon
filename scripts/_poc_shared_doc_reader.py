#!/usr/bin/env python3
"""共享在线文档读取层 POC —— 平台无关「表 → 中文表头 dict 行」

目标(对应《共享文档数据同步系统建设方案》快速验证):
  证明「任意来源的共享表格(腾讯/金山/本地 xlsx) → 复用现有 complaint_sync
  清洗/指纹去重/SN关联工单/入库 管线」这条链路成立, 为后续"批量共享文档
  导入"框架打样。

设计要点:
  1. 读取层只负责把一张 Sheet 变成 [ {中文列名: 值}, ... ] 的行列表;
  2. 入库统一交给 complaint_sync.sync_complaints_from_payload(它内部用
     COLUMN_TEMPLATES 做 中文列名 → 模型字段 映射, 与平台无关);
  3. 浏览器来源(腾讯/金山共享表格)统一走「打开链接 → 触发导出 xlsx →
     拦截下载 → 本地解析」, 不逐格读 canvas DOM(见共享文档建设方案 3.1)。

用法(当前 POC = 本地 xlsx 直读, 不依赖浏览器/账号):
  cd backend && ./.venv/bin/python ../scripts/_poc_shared_doc_reader.py \
      --file ../01样本数据/315投诉.xlsx
"""
from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path
from typing import Any

# 允许从仓库根或 backend 目录运行
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

import pandas as pd  # 与 data_parser 同款引擎(calamine/openpyxl)

# 统一用 calamine 引擎: 快 + 免疫 WPS/腾讯导出的 openpyxl 样式解析崩溃
# (本地 315投诉.xlsx 实测: openpyxl 读 stylesheet 抛 Fill() TypeError)
_EXCEL_ENGINE = "calamine"


def _norm_header(h: Any) -> str:
    if h is None:
        return ""
    s = str(h).strip().replace("\u3000", " ").replace("\n", "")
    # Excel 首行若为合并单元格, pandas 会给 "Unnamed: N", 这类不视为真实表头
    if s.startswith("Unnamed:"):
        return ""
    return s


def read_sheet_to_records(
    file_path: str | Path,
    sheet_name: str | None = None,
    header_row: int = 0,
) -> list[dict[str, Any]]:
    """读取 xlsx/csv 的指定 Sheet → [ {中文表头: 值}, ... ]

    只负责"取数", 不做字段映射/清洗(那是 complaint_sync 的事)。
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, engine=_EXCEL_ENGINE)
    # 丢掉全空列(合并表头/末尾多余列常产生整列 NaN)
    df = df.dropna(axis=1, how="all")
    # 表头归一化, 丢弃空表头列
    df.columns = [_norm_header(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c != ""]]
    # 行: NaN → None(保证 json/dict 干净, 且 complaint_sync 判空逻辑可识别)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rec = {str(col): (None if pd.isna(v) else v) for col, v in row.items()}
        records.append(rec)
    return records


def dump_sheets(file_path: str | Path) -> list[str]:
    """列出文件内所有 Sheet 名(供调用方按需选表)"""
    xl = pd.ExcelFile(file_path, engine=_EXCEL_ENGINE)
    return list(xl.sheet_names)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="共享在线文档读取层 POC")
    ap.add_argument("--file", required=True, help="xlsx/csv 路径(共享表格导出或本地样本)")
    args = ap.parse_args()

    f = Path(args.file)
    if not f.exists():
        print(f"❌ 文件不存在: {f}")
        sys.exit(2)

    print(f"📄 {f.name} 的 Sheet 列表: {dump_sheets(f)}")
    # POC: 打印每个 sheet 首表 + 行数, 验证"读取层 → dict 行"成立
    for sn in dump_sheets(f):
        recs = read_sheet_to_records(f, sheet_name=sn)
        print(f"\n=== Sheet [{sn}] → {len(recs)} 行 ===")
        if recs:
            head = recs[0]
            # 只打印前 12 个字段, 避免刷屏
            preview = {k: (str(v)[:12] + "…" if len(str(v)) > 12 else v) for k, v in list(head.items())[:12]}
            print("首行列头样例:", preview)
        else:
            print("  (空表, 无数据行)")
