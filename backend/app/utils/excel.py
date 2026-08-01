"""Excel 写入安全工具

openpyxl 写入 xlsx 时若单元格字符串含有 ASCII 控制字符
(U+0000 ~ U+001F, 除 tab U+0009 / LF U+000A / CR U+000D 三个合法空白)
或 DEL U+007F, 会抛 IllegalCharacterError: "xxx cannot be used in worksheets."

本模块提供两个入口,分别覆盖:
  - 单值清洗: sanitize_cell_value(v)  -> 用于逐字段构建 records dict 时兜底
  - DataFrame 全表清洗: clean_df_for_excel(df) -> 用于写 xlsx 前对 df 批量处理

典型调用场景:
  1. data_sources.export_records: 通用导出(浏览/过滤后的全量原始记录)
  2. ai_acc_fetcher.fetch_one: BMS/ai-acc 数据同步时把分片合并后的 merged df 落盘 xlsx
"""

from __future__ import annotations

import pandas as pd

# 清洗规则:
#   - 移除 C0 控制字符 U+0000..U+001F, 但保留 HT(0x09)/LF(0x0A)/CR(0x0D)
#   - 移除 DEL U+007F
# str.maketrans 比每次 re.sub 快,对 2w+ 行的 df 而言耗时可忽略
_XLSX_STRIP_CTRL_TRANS = str.maketrans(
    "",
    "",
    "".join(
        chr(c)
        for c in range(0x00, 0x80)
        if (c < 0x20 and c not in (0x09, 0x0A, 0x0D)) or c == 0x7F
    ),
)


def sanitize_cell_value(value):
    """对单个单元格值做 Excel 安全清洗,非字符串原值返回"""
    if not isinstance(value, str):
        return value
    # 先快速路径: 任何 <32 或 =127 的字符出现才 translate
    for ch in value:
        code = ord(ch)
        if (code < 0x20 and code not in (0x09, 0x0A, 0x0D)) or code == 0x7F:
            return value.translate(_XLSX_STRIP_CTRL_TRANS)
    return value


def clean_df_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """对 DataFrame 里所有 object/string 列逐值清洗 Excel 非法控制字符,原地修改并返回 df"""
    if df is None or df.empty:
        return df
    for col in df.columns:
        series = df[col]
        # 只处理对象列或显式 StringDtype
        if series.dtype == object or pd.api.types.is_string_dtype(series):
            # apply + 单值函数:对 2w~10w 级数据量足够快
            df[col] = series.apply(sanitize_cell_value)
    return df
