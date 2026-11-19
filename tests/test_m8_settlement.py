from decimal import Decimal as D
from app.pipeline import run
from app.normalize import instant
from app.modules.m4_conflicts.rules import conflicts as find_conflicts
from app.modules.m5_calendar.rules import calendar
from app.modules.m6_reconcile.rules import reconcile
from app.modules.m8_settlement.rules import settlement

def iv(patient, ward, bed, start, end):
    return dict(patient=patient, ward=ward, bed=bed, start=instant(start), end=instant(end), open=False, source='t')

def build(intervals, conflicts=None):
    cal = calendar(intervals)
    return settlement(intervals, cal, reconcile(intervals, cal), conflicts if conflicts is not None else find_conflicts(intervals))

def test_same_day_transfer_one_bed_day():
    x = build([iv('P1', '一病区', '001', '2026-11-16T08:00:00+08:00', '2026-11-16T10:00:00+08:00'),
               iv('P1', '二病区', '101', '2026-11-16T10:00:00+08:00', '2026-11-16T18:00:00+08:00')])
    assert len(x['rows']) == 1 and x['totals']['bed_days'] == 1
    row = x['rows'][0]
    assert row['ward'] == '二病区' and row['minutes'] == 600
    assert {d['ward']: d['minutes'] for d in row['detail']} == {'一病区': D(120), '二病区': D(480)}

def test_tie_goes_to_handover_ward():
    x = build([iv('P1', '一病区', '001', '2026-11-16T08:00:00+08:00', '2026-11-16T14:00:00+08:00'),
               iv('P1', '二病区', '101', '2026-11-16T14:00:00+08:00', '2026-11-16T20:00:00+08:00')])
    row = x['rows'][0]
    assert row['ward'] == '二病区' and '并列' in row['basis'] and '交接' in row['basis']

def test_short_stay_still_counts():
    x = build([iv('P1', '一病区', '001', '2026-11-16T09:00:00+08:00', '2026-11-16T09:10:00+08:00')])
    assert x['totals']['bed_days'] == 1 and x['rows'][0]['minutes'] == 10

def test_midnight_split_one_row_per_day():
    x = build([iv('P1', '一病区', '001', '2026-11-16T22:00:00+08:00', '2026-11-17T03:00:00+08:00')])
    assert [r['minutes'] for r in x['rows']] == [120, 180] and x['totals']['bed_days'] == 2

def test_ward_minutes_match_calendar():
    x = build([iv('P1', '一病区', '001', '2026-11-16T08:00:00+08:00', '2026-11-16T12:00:00+08:00'),
               iv('P1', '二病区', '101', '2026-11-16T12:00:00+08:00', '2026-11-17T08:00:00+08:00'),
               iv('P2', '一病区', '002', '2026-11-16T00:00:00+08:00', '2026-11-18T00:00:00+08:00')])
    assert all(d['state'] == '通过' for d in x['ward_days'])
    assert all(t['state'] == '通过' for t in x['ward_totals'])
    assert sum(d['bed_days'] for d in x['ward_days']) == len(x['rows'])
    assert sum(t['minutes'] for t in x['ward_totals']) == x['totals']['minutes']

def test_conflict_flags_pending():
    intervals = [iv('P1', '一病区', '001', '2026-11-16T08:00:00+08:00', '2026-11-16T20:00:00+08:00'),
                 iv('P2', '一病区', '001', '2026-11-16T10:00:00+08:00', '2026-11-16T12:00:00+08:00')]
    x = build(intervals)
    assert {r['patient'] for r in x['pending']} == {'P1', 'P2'}
    assert all(r['state'] == '待复核' for r in x['rows'])

def test_real_one_row_per_patient_day(raw):
    x = run(raw, persist=False)['settlement']
    keys = [(r['date'], r['patient']) for r in x['rows']]
    assert len(keys) == len(set(keys)) and x['totals']['bed_days'] == len(keys) > 0

def test_real_minutes_tie_out(raw):
    r = run(raw, persist=False); x = r['settlement']
    assert D(x['totals']['minutes']) == sum(D(b['minutes']) for b in r['calendar'])
    assert all(d['state'] == '通过' for d in x['ward_days'])
    assert all(t['state'] == '通过' for t in x['ward_totals'])

def test_real_pending_matches_conflicts(raw):
    r = run(raw, persist=False)
    assert len(r['conflicts']) > 0 and len(r['settlement']['pending']) > 0

def test_replay_same_batch_no_duplicate(raw, isolate):
    from app import store
    a = run(raw); b = run(raw)
    assert a['manifest']['run_id'] == b['manifest']['run_id']
    assert a['settlement']['rows'] == b['settlement']['rows']
    assert sum(h['id'] == a['manifest']['run_id'] for h in store.history()) == 1

def test_review_does_not_overwrite_events(raw, isolate):
    from app import store
    a = run(raw)
    store.review(a['manifest']['run_id'], a['version'], '通过')
    b = run(raw)
    assert b['review'] == '通过'
    assert b['records'] == a['records'] and b['settlement']['rows'] == a['settlement']['rows']

def test_late_batch_appends_run_keeps_old(raw, isolate):
    from app import store
    a = run(raw)
    extra = b'\n88801,08881,002,W1,2026-11-17T10:00:00+08:00,admit,1\n'
    b = run(raw.rstrip(b'\n') + extra)
    assert a['manifest']['run_id'] != b['manifest']['run_id']
    old = store.fetch(a['manifest']['run_id'])
    assert old['settlement']['rows'] == a['settlement']['rows'] and old['records'] == a['records']

def test_real_export_matches_result(client):
    r = client.post('/api/runs', json={}).json
    text = client.get('/api/settlement.csv?run=' + r['manifest']['run_id']).text
    assert '结算床日' in text and '病区合计' in text
    lines = [l for l in text.splitlines() if l.startswith('结算床日')]
    assert len(lines) == r['settlement']['totals']['bed_days']
