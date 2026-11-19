from collections import defaultdict
from datetime import timedelta, timezone, datetime
from decimal import Decimal as D

ZONE = timezone(timedelta(hours=8))

def _split(item):
    cursor = item['start']; end = item['end']
    while cursor < end:
        local = cursor.astimezone(ZONE)
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), ZONE)
        stop = min(end, midnight)
        yield local.date().isoformat(), stop, D(str((stop - cursor).total_seconds())) / 60
        cursor = stop

def _affected(intervals, conflicts):
    days = set()
    for conflict in conflicts:
        patients = set(conflict['patient'].split(' / '))
        for item in intervals:
            if item['bed'] != conflict['bed'] or item['patient'] not in patients: continue
            cursor = max(item['start'], conflict['start']); end = min(item['end'], conflict['end'])
            while cursor < end:
                local = cursor.astimezone(ZONE)
                days.add((local.date().isoformat(), item['patient']))
                midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), ZONE)
                cursor = min(end, midnight)
    return days

def settlement(intervals, calendar, reconciliation, conflicts):
    minutes = defaultdict(lambda: D(0)); last_end = {}
    for item in intervals:
        for date, stop, value in _split(item):
            key = (date, item['patient'], item['ward'])
            minutes[key] += value
            if key not in last_end or stop > last_end[key]: last_end[key] = stop
    days = defaultdict(dict)
    for (date, patient, ward), value in minutes.items(): days[(date, patient)][ward] = value
    flagged = _affected(intervals, conflicts)
    rows = []
    for (date, patient), wards in sorted(days.items()):
        total = sum(wards.values(), D(0))
        if total <= 0: continue
        order = sorted(wards.items(), key=lambda kv: (-kv[1], -last_end[(date, patient, kv[0])].timestamp(), kv[0]))
        ward, best = order[0]
        tied = [w for w, value in order if value == best]
        if len(wards) == 1: basis = f'当日仅{ward}占床'
        elif len(tied) == 1: basis = f'{ward}占床{best}min为当日最长'
        elif len({last_end[(date, patient, w)] for w in tied}) > 1: basis = f'占床分钟并列，归交接时所在{ward}'
        else: basis = f'分钟与交接时刻并列，按病区序归{ward}'
        detail = [{'ward': w, 'minutes': value} for w, value in sorted(wards.items())]
        rows.append({'date': date, 'patient': patient, 'ward': ward, 'minutes': total, 'detail': detail,
                     'wards_text': ' · '.join(f"{x['ward']} {x['minutes']}min" for x in detail),
                     'basis': basis, 'state': '待复核' if (date, patient) in flagged else '通过'})
    day_minutes = defaultdict(lambda: D(0)); day_bed_days = defaultdict(int)
    for row in rows:
        day_bed_days[(row['date'], row['ward'])] += 1
        for part in row['detail']: day_minutes[(row['date'], part['ward'])] += part['minutes']
    calendar_days = defaultdict(lambda: D(0))
    for bucket in calendar: calendar_days[(bucket['date'], bucket['ward'])] += bucket['minutes']
    ward_days = []
    for date, ward in sorted(set(day_minutes) | set(day_bed_days) | set(calendar_days)):
        value, expect = day_minutes[(date, ward)], calendar_days[(date, ward)]
        ward_days.append({'date': date, 'ward': ward, 'bed_days': day_bed_days[(date, ward)],
                          'minutes': value, 'calendar_minutes': expect, 'delta': value - expect,
                          'state': '通过' if value == expect else '差异'})
    recon = {row['ward']: row for row in reconciliation}
    names = sorted(set(recon) | {ward for _, ward in day_minutes} | {ward for _, ward in day_bed_days} | {b['ward'] for b in calendar})
    ward_totals = []
    for ward in names:
        value = sum((v for (_, w), v in day_minutes.items() if w == ward), D(0))
        count = sum((c for (_, w), c in day_bed_days.items() if w == ward), 0)
        expect = sum((b['minutes'] for b in calendar if b['ward'] == ward), D(0))
        episode = recon[ward]['episode_minutes'] if ward in recon else D(0)
        ward_totals.append({'ward': ward, 'bed_days': count, 'minutes': value, 'calendar_minutes': expect,
                            'episode_minutes': episode, 'delta': value - expect,
                            'state': '通过' if value == expect == episode else '差异'})
    pending = [{'date': row['date'], 'patient': row['patient'], 'ward': row['ward'], 'minutes': row['minutes'],
                'reason': '涉及双占冲突，占床分钟待人工复核', 'state': row['state']} for row in rows if row['state'] == '待复核']
    pending += [{'date': day['date'], 'patient': '—', 'ward': day['ward'], 'minutes': day['minutes'],
                 'reason': f"病区日分钟与占床日历差异 {day['delta']}min", 'state': '待复核'} for day in ward_days if day['state'] != '通过']
    totals = {'bed_days': len(rows), 'patient_days': len(rows), 'minutes': sum((row['minutes'] for row in rows), D(0))}
    return {'rows': rows, 'ward_days': ward_days, 'ward_totals': ward_totals, 'pending': pending, 'totals': totals}
