"""评估 CSV 截断行: BMS 严格跳过 vs data_parser 修复 对比

对每个 CSV 文件:
1. BMS 严格校验(复刻 bms_fetcher._crop_refund_by_date_range CSV 分支):
   逐行 csv.reader + 去尾\\t + 去末尾空字段 + 字段数==表头数才算通过
2. data_parser 修复(复刻 read_csv_auto_encoding 核心循环):
   按对齐方式分类统计, 并关联 strict 跳过的行

用法: python scripts/eval_csv_repair.py <csv1> <csv2> ...
"""
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path


# ---------- 复刻 data_parser 的修复原语 ----------
def split_fields(text: str) -> list[str]:
    fields, buf = [], []
    in_quote = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_quote:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                in_quote = False
            else:
                buf.append(ch)
            i += 1
        else:
            if ch == '"':
                in_quote = True
                i += 1
            elif ch == ',':
                fields.append(''.join(buf))
                buf = []
                i += 1
            else:
                buf.append(ch)
                i += 1
    if buf:
        fields.append(''.join(buf))
    return fields


def fix_unclosed_quote(line: str) -> str:
    pos = line.find('""')
    while pos > 0:
        if pos + 2 < len(line) and line[pos + 2] == ',':
            before = line[:pos]
            if before.count('"') % 2 != 0:
                line = line[:pos + 2] + '"' + line[pos + 2:]
        pos = line.find('""', pos + 2)
    return line


def fix_unclosed_quote_in_long_field(line: str) -> str:
    in_quote = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if in_quote:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    i += 2
                    continue
                in_quote = False
                i += 1
            else:
                if ch == ',' and i + 11 <= n:
                    rest = line[i + 1:i + 11]
                    if re.match(r'\d{4}-\d{2}-\d{2}', rest):
                        return line[:i] + '"' + line[i:]
                i += 1
        else:
            if ch == '"':
                in_quote = True
            i += 1
    return line


# ---------- 评估 ----------
def greedy_join_analysis(header_len: int, data_lines: list[str]) -> dict:
    """模拟"贪婪拼接后续行直到字段数>=表头", 分类统计截断行恢复情况。

    回答: 为什么 data_parser 只拼1行会漏——部分截断行需拼2行以上才够字段。
    返回各拼接行数的计数 + 拼接后恰好对齐/超出表头的分布。
    """
    cnt = Counter()            # 拼接消耗的行数(1,2,3...) → 条数
    still_short = 0            # 拼完全部后续行仍 < 表头
    exact = 0                  # 拼接后字段数 == 表头(完美恢复)
    over = 0                   # 拼接后字段数 > 表头(拼过头, 需拆)
    i = 0
    n = len(data_lines)
    while i < n:
        line = data_lines[i].strip()
        if not line:
            i += 1
            continue
        fields = split_fields(fix_unclosed_quote(line))
        if len(fields) == header_len:
            i += 1
            continue
        # 截断/错位行: 贪婪拼接后续行
        joined = line
        used = 1
        j = i + 1
        while len(fields) < header_len and j < n:
            nxt = data_lines[j].strip()
            j += 1
            if not nxt:
                continue
            joined = joined + ' ' + nxt
            fields = split_fields(fix_unclosed_quote(joined))
            used += 1
        if len(fields) < header_len:
            still_short += 1
        elif len(fields) == header_len:
            exact += 1
            cnt[used] += 1
        else:
            over += 1
        i = j  # 跳到拼接的最后一行之后
    return {"still_short": still_short, "exact": exact, "over": over, "cnt": dict(cnt)}


def repair_validate_analysis(header_len: int, data_lines: list[str]) -> dict:
    """模拟 data_parser.repair_csv_blocks 的完整流程: 修复+校验

    统计:
    - repaired: 恢复的总行数(拼接后通过校验)
    - validated: 其中纯校验通过的行(无需拼接)
    - skipped: 校验失败的行
    """
    _RE_REFUND_NO = re.compile(r'^(RF|RB|RE)\d{10,}')

    def _validate(fields):
        return bool(fields and _RE_REFUND_NO.match(str(fields[0]).strip()))

    repaired = 0
    validated = 0
    skipped = 0
    i = 0
    n = len(data_lines)

    while i < n:
        line = data_lines[i].strip()
        if not line:
            i += 1
            continue

        line = fix_unclosed_quote(line)
        fields = split_fields(line)

        if len(fields) < header_len:
            fixed = fix_unclosed_quote_in_long_field(line)
            if fixed != line:
                ff = split_fields(fixed)
                if len(ff) > len(fields):
                    line = fixed
                    fields = ff

        # 贪婪拼接
        joined = line
        j = i + 1
        while len(fields) < header_len and j < n:
            nxt = data_lines[j].strip()
            j += 1
            if not nxt:
                continue
            joined = joined + ' ' + nxt
            joined = fix_unclosed_quote(joined)
            fields = split_fields(joined)

        # BMS 清洗
        fields = [f.rstrip("\t") for f in fields]
        while len(fields) > header_len and fields[-1] == "":
            fields.pop()

        # 合并多余
        if len(fields) > header_len:
            extra = fields[header_len - 1:]
            fields = fields[:header_len - 1] + [','.join(extra)]

        # 补齐
        if len(fields) < header_len:
            fields = fields + [""] * (header_len - len(fields))

        if _validate(fields):
            repaired += 1
            if j == i + 1:  # 没拼接
                validated += 1
        else:
            skipped += 1

        i = j

    return {"repaired": repaired, "validated": validated, "skipped": skipped}


def analyze(path: Path) -> dict:
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    header = [str(c).replace("\t", "").strip() for c in split_fields(lines[0])]
    header_len = len(header)
    data_lines = lines[1:]
    n_data = len(data_lines)

    # 1) BMS 严格校验
    strict_ok_idx = set()
    strict_skip = 0
    for idx, line in enumerate(data_lines):
        line = line.strip()
        if not line:
            continue
        try:
            fields = next(csv.reader(io.StringIO(line)))
        except (StopIteration, csv.Error):
            strict_skip += 1
            continue
        fields = [f.rstrip("\t") for f in fields]
        while len(fields) > header_len and fields[-1] == "":
            fields.pop()
        if len(fields) != header_len:
            strict_skip += 1
            continue
        strict_ok_idx.add(idx)

    # 1.5) 原始字段数分布(用 data_parser 的 split_fields, 不做任何修复)
    field_dist = Counter(len(split_fields(l.strip())) for l in data_lines if l.strip())

    # 2) data_parser 修复(复刻其逐行循环), 按对齐方式分类
    align = {"natural": 0, "merged": 0, "merged_extra": 0, "padded": 0, "bad": 0}
    # merged 中起始行是 strict 会跳过的行数(即真正新恢复的数据)
    merged_recovered = 0
    # 记录各分类的行示例(首行号, 概要)便于人工抽查
    samples = {k: [] for k in align}

    def sample(key, idx, fields, tag=""):
        if len(samples[key]) < 3:
            head = " | ".join(str(f)[:20] for f in fields[:4])
            tail = " || ".join(str(f)[:30] for f in fields[-2:])
            samples[key].append((idx, tag, f"n={len(fields)} head[{head}] tail[{tail}]"))

    i = 0
    while i < n_data:
        start_idx = i
        line = data_lines[i].strip()
        if not line:
            i += 1
            continue

        line = fix_unclosed_quote(line)
        fields = split_fields(line)

        if len(fields) < header_len:
            fixed = fix_unclosed_quote_in_long_field(line)
            if fixed != line:
                ff = split_fields(fixed)
                if len(ff) > len(fields):
                    line = fixed
                    fields = ff

        merged = False
        if len(fields) < header_len:
            if i + 1 < n_data:
                nxt = data_lines[i + 1].strip()
                if nxt:
                    line = line + ' ' + nxt
                    line = fix_unclosed_quote(line)
                    fields = split_fields(line)
                    i += 1
                    merged = True

        extra_merged = False
        if len(fields) > header_len:
            extra = fields[header_len - 1:]
            fields = fields[:header_len - 1] + [','.join(extra)]
            extra_merged = True

        padded = False
        if len(fields) < header_len:
            fields = fields + [""] * (header_len - len(fields))
            padded = True

        if len(fields) == header_len:
            if extra_merged:
                key = "merged_extra"
            elif padded:
                key = "padded"
            elif merged:
                key = "merged"
            else:
                key = "natural"
            align[key] += 1
            if key == "merged" and start_idx not in strict_ok_idx:
                merged_recovered += 1
            sample(key, start_idx, fields, "merged" if merged else "")
        else:
            align["bad"] += 1
            sample("bad", start_idx, fields)
        i += 1

    return {
        "path": path.name,
        "header_len": header_len,
        "n_data": n_data,
        "strict_ok": len(strict_ok_idx),
        "strict_skip": strict_skip,
        "align": align,
        "merged_recovered": merged_recovered,
        "field_dist": field_dist,
        "samples": samples,
    }


def main():
    files = [Path(p) for p in sys.argv[1:]]
    print(f"{'文件':<42}{'字段':>4}{'行数':>7}{'严格通过':>8}{'严格跳过':>8}"
          f"{'天然对齐':>8}{'跨行合并':>8}{'多余合并':>8}{'补空串':>7}{'未对齐':>7}"
          f"{'合并恢复(新)':>11}  {'修复校验':>8}{'跳过':>6}")
    print("-" * 145)
    for f in files:
        r = analyze(f)
        # 修复+校验结果
        content = Path(f).read_text(encoding="utf-8-sig", errors="replace")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        rv = repair_validate_analysis(r["header_len"], lines[1:]) if r["strict_skip"] > 0 else {"repaired": r["n_data"], "validated": r["n_data"], "skipped": 0}
        a = r["align"]
        print(f"{r['path'][:42]:<42}{r['header_len']:>4}{r['n_data']:>7}"
              f"{r['strict_ok']:>8}{r['strict_skip']:>8}"
              f"{a['natural']:>8}{a['merged']:>8}{a['merged_extra']:>8}"
              f"{a['padded']:>7}{a['bad']:>7}{r['merged_recovered']:>11}"
              f"  {rv['repaired']:>8}{rv['skipped']:>6}")

    # 打印典型样本
    for f in files:
        r = analyze(f)
        print(f"\n=== {r['path']} ===")
        print(f"  原始字段数分布: {dict(sorted(r['field_dist'].items()))}")
        # 贪婪拼接分析(仅对存在截断的文件显示)
        if r["strict_skip"] > 0:
            content = Path(f).read_text(encoding="utf-8-sig", errors="replace")
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            lines = content.split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            g = greedy_join_analysis(r["header_len"], lines[1:])
            print(f"  贪婪拼接: 完美恢复={g['exact']} 拼1行={g['cnt'].get(1,0)} "
                  f"拼2行={g['cnt'].get(2,0)} 拼3行={g['cnt'].get(3,0)} "
                  f"拼4+行={sum(v for k,v in g['cnt'].items() if k>=4)} "
                  f"拼过头={g['over']} 仍不足={g['still_short']}")
            # 修复+校验结果
            rv = repair_validate_analysis(r["header_len"], lines[1:])
            print(f"  修复+校验: 恢复={rv['repaired']} 首次通过={rv['validated']} "
                  f"校验跳过={rv['skipped']}")
        for key in ("merged", "padded", "merged_extra", "bad"):
            if r["samples"][key]:
                print(f"  [{key}] 示例:")
                for idx, tag, snip in r["samples"][key][:3]:
                    print(f"    行{idx} {tag}  {snip}")


if __name__ == "__main__":
    main()
