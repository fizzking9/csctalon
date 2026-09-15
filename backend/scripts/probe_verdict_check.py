"""共享文档「版本探测闸门」判据对照实验（工单 #109 验证用）

**用途**：验证 `probe_doc_version()` 返回的版本串，是否与「入库内容」真实变化一致。
这是开启 `SKIP_EXPORT_IF_UNCHANGED` 前置条件 —— 若判据会**漏报**（内容变了但
版本串没变），开启后会静默漏采，后果比不优化更严重。

**成本控制**：每轮先做 0.2s 轻量探测；**只有版本串变了才导出算内容指纹**（约 65s）。
版本未变的轮次零成本，可直接放进后台循环长时间观察。

**用法**（生产容器内，只读，不写业务表）：
    docker exec -e PYTHONPATH=/app csctalon-backend \
        python /app/scripts/probe_verdict_check.py

**判读**：
- `✅ 一致`     版本变 且 内容也变  → 判据可信
- `❌ 虚警`     版本变 但 内容没变  → 安全（只多花导出时间），可接受
- `🚨 漏报`     内容变 但 版本没变  → **危险，不得开启开关**（会漏采）

**⚠️ 两种模式（关键）**：
- 默认（省成本）：版本未变就不导出 → **只能发现虚警，发现不了漏报**；
- `--always-export`：每轮都导出算指纹 → **能发现漏报**。
  开启开关前的验证**必须**用这个模式，否则等于没验最危险的那一面。
  代价是每轮 ~65s，属验证期必要成本。

快照落在 BROWSER_STATE_DIR（挂载卷），容器重建不丢。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 允许以 `python scripts/probe_verdict_check.py` 直接运行
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import shared_doc_sync_service as sdss  # noqa: E402

SNAP = Path(sdss.settings.BROWSER_STATE_DIR) / ".probe_verdict_snapshot.json"

# 从采集源配置动态取文档(优先退款源, 回退第一行 kdocs 源), 避免硬编码链接
_DOC_URL = os.environ.get("PROBE_DOC_URL", "https://www.kdocs.cn/l/cbZIHtt6obyf")
_STATE_FILE = os.environ.get("PROBE_STATE_FILE", "kdocs.json")

# 入库不使用的列 —— 指纹必须排除, 否则「只改图片」也会被判为内容变化
_DROP_COLS = {"图片", "图片2"}


def content_fingerprint() -> tuple[str, int]:
    """导出一份并算「入库字段」指纹(排除图片列)。"""
    out = tempfile.mktemp(suffix=".xlsx")
    try:
        sdss.export_doc_xlsx("kdocs", _DOC_URL, out, _STATE_FILE)
        rows = sdss._pick_manual_refund_sheet(out, None)
    finally:
        if os.path.exists(out):
            os.remove(out)
    norm = [
        {k: ("" if v is None else str(v).strip()) for k, v in r.items() if k not in _DROP_COLS}
        for r in rows
    ]
    norm.sort(key=lambda d: (d.get("登记日期", ""), d.get("订单号", ""), d.get("设备号", "")))
    digest = hashlib.sha256(
        json.dumps(norm, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]
    return digest, len(rows)


def main() -> int:
    # --always-export: 每轮都导出算指纹。这是**唯一能发现「漏报」**的模式
    # (默认模式在版本未变时不导出, 于是"内容偷偷变了但版本没变"永远看不到)。
    always = "--always-export" in sys.argv
    version = sdss.probe_doc_version("kdocs", _DOC_URL, _STATE_FILE)
    stamp = time.strftime("%m-%d %H:%M:%S")
    if version is None:
        print(f"[{stamp}] 探测不可用 → 照常导出路径(本轮不参与对照)", flush=True)
        return 0

    prev = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else None
    version_changed = bool(prev) and prev.get("version") != version

    if prev and not version_changed and not always:
        print(f"[{stamp}] 版本未变 {version} → 不导出(省 ~65s)", flush=True)
        return 0

    t0 = time.time()
    fp, n_rows = content_fingerprint()
    elapsed = time.time() - t0
    content_changed = bool(prev) and prev.get("fp") != fp

    if prev is None:
        verdict = "基线"
    elif version_changed and content_changed:
        verdict = "✅ 一致(版本变且内容也变)"
    elif version_changed and not content_changed:
        verdict = "❌ 虚警(版本变但内容没变 → 只多花时间, 不漏采)"
    elif not version_changed and content_changed:
        # 🚨 最危险: 判据漏报。开启开关会导致这部分内容永不入库。
        verdict = "🚨 漏报(内容变了但版本没变 → 严禁开启开关!)"
    else:
        verdict = "✓ 都未变"

    print(
        f"[{stamp}] {version} (上次 {prev.get('version') if prev else '-'}) "
        f"→ 导出 {elapsed:.0f}s  指纹 {fp}(上次 {prev.get('fp') if prev else '-'}) "
        f"行数 {n_rows}  → {verdict}",
        flush=True,
    )
    SNAP.write_text(
        json.dumps({"version": version, "fp": fp, "rows": n_rows, "at": time.time()}),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
