"""Verify 8 urge_analysis manual handlers work via analysis_engine dispatcher."""
import sys
import traceback

sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.services.analysis_engine import execute_view
from app.services.urge_analysis import HANDLERS
from app.models.analysis_view import AnalysisView

print('HANDLERS registered:', len(HANDLERS))
print()

codes = [
    'preset_refund_urge_kpi',
    'preset_refund_urge_bucket',
    'preset_refund_urge_person_top',
    'preset_refund_urge_scatter',
    'preset_refund_urge_top50',
    'preset_complaint_urge_trend',
    'preset_complaint_urge_conversion',
    'preset_complaint_urge_contribution',
]

ok = 0
fail = 0
for code in codes:
    db = SessionLocal()
    try:
        view = db.query(AnalysisView).filter(AnalysisView.code == code).first()
        assert view is not None, 'view code not found: ' + code
        result = execute_view(db, view, params=None)
        ct = result.get('chart_type')
        data = result.get('data')
        if ct == 'table':
            rows = data.get('rows', []) if isinstance(data, dict) else []
            cols = data.get('columns', []) if isinstance(data, dict) else []
            extra = ''
            if rows and isinstance(rows[0], dict):
                r0 = rows[0]
                sample = [f'{k}={r0[k]}' for k in list(r0.keys())[:3]]
                extra = ' | row0: ' + ' | '.join(sample)
            print(f'OK  {code:<45s} TABLE cols={len(cols):2d} rows={len(rows):3d}{extra}')
        elif ct == 'bar':
            xs = data.get('x_axis', []) if isinstance(data, dict) else []
            series = data.get('series', []) if isinstance(data, dict) else []
            names = [s.get('name', '?') for s in series]
            print(f'OK  {code:<45s} BAR   x_items={len(xs):3d} series={names}')
        elif ct == 'pie':
            items = data.get('items', []) if isinstance(data, dict) else []
            tot = sum(i.get('value', 0) for i in items)
            names = [i.get('name', '?') for i in items]
            print(f'OK  {code:<45s} PIE   total={tot} items={names}')
        else:
            print(f'OK  {code:<45s} chart_type={ct} keys={list(result.keys())[:5]}')
        ok += 1
    except Exception as e:
        print(f'FAIL {code:<45s} {type(e).__name__}: {e}')
        traceback.print_exc()
        fail += 1
    finally:
        db.rollback()
        db.close()

print()
print(f'========== Result: PASS {ok} / FAIL {fail} / TOTAL {ok + fail} ==========')
sys.exit(0 if fail == 0 else 1)
