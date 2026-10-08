"""版本判据 vs 真实入库 对照（工单 #109 开启开关前的验证）

**与 probe_verdict_check.py 的区别**：本脚本跑的是**真实同步**（`sync_one_source`），
每次都会真正采集入库 —— 既完成正常业务采集，又同时积累「判据是否可信」的证据，
不额外浪费导出成本。

**验证逻辑**（每轮）：
  1. 轻量探测 digest（0.2s）；
  2. 跑真实同步（照常导出+入库，~65s）；
  3. 读回本次 `inserted/updated` 与 summary 里的 digest；
  4. 判读：
     - digest 未变 但 inserted/updated > 0  → 🚨 **漏报，严禁开启开关**
     - digest 变了 但 inserted=updated=0    → ❌ 虚警（安全，只多花时间）
     - digest 变 且 有写入                 → ✅ 一致
     - digest 未变 且 无写入               → ✓ 都未变（正是我们要省的那部分）

**用法**（生产容器内）：
    docker exec -e PYTHONPATH=/app csctalon-backend \
        python /app/scripts/verdict_vs_ingest.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.database import SessionLocal  # noqa: E402
from app.services import shared_doc_sync_service as sdss  # noqa: E402

SNAP = Path(sdss.settings.BROWSER_STATE_DIR) / ".verdict_ingest_snapshot.json"
SOURCE_ID = int(__import__("os").environ.get("VERDICT_SOURCE_ID", "2"))


def main() -> int:
    stamp = time.strftime("%m-%d %H:%M:%S")
    db = SessionLocal()
    try:
        row = sdss.get_doc_source(db, SOURCE_ID)
        if row is None:
            print(f"[{stamp}] 采集源不存在: id={SOURCE_ID}", flush=True)
            return 1

        before_digest = sdss.probe_doc_version(row.platform, row.doc_url, sdss._state_file_for(row))
        prev = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else None

        # 真实同步(照常导出+入库)
        t0 = time.time()
        try:
            stats = sdss.sync_one_source(db, row, trigger="verdict-check")
            err = None
        except Exception as e:  # noqa: BLE001
            stats, err = None, f"{type(e).__name__}: {e}"
        elapsed = time.time() - t0

        if err:
            print(f"[{stamp}] 同步失败({elapsed:.0f}s): {err[:120]}", flush=True)
            return 0

        ins = stats.get("inserted") or 0
        upd = stats.get("updated") or 0
        wrote = ins + upd

        if prev is None:
            verdict = "基线"
        else:
            d_changed = prev.get("digest") != before_digest
            if not d_changed and wrote > 0:
                verdict = "🚨 漏报(判据未变但确实写了库 → 严禁开启开关!)"
            elif d_changed and wrote == 0:
                verdict = "❌ 虚警(判据变了但没写库 → 只多花时间, 安全)"
            elif d_changed and wrote > 0:
                verdict = "✅ 一致(判据变且确实写了库)"
            else:
                verdict = "✓ 都未变(正是开关要省的那部分)"

        print(
            f"[{stamp}] digest={str(before_digest)[:16]}"
            f" (上次 {str(prev.get('digest'))[:16] if prev else '-'})"
            f" → 同步 {elapsed:.0f}s ins={ins} upd={upd} status={stats.get('status')}"
            f"  → {verdict}",
            flush=True,
        )
        SNAP.write_text(json.dumps({
            "digest": before_digest, "ins": ins, "upd": upd, "at": time.time(),
        }), encoding="utf-8")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
