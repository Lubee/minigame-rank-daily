"""Recover the known WX tag shift, preserving other platforms and raw backups."""
import copy
import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import base
from release_monitor import save

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/wx-field-repair-2026-11-24'
PRIMARY = re.compile(r'(.+?)\s*[:：·]\s*(\d+)\s*名')
# Subcategory values observed in the affected snapshots; do not migrate ambiguous rows.
SUBCATEGORIES = {'益智', '消除', '模拟经营', '塔防', '牌类', '卡牌', 'ARPG', '其他',
                 'MMO', '对战', '棋类', '回合', '枪战', '战争策略', '竞速'}


def backup(path):
    target = OUT / 'before' / path.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not target.exists():
        shutil.copy2(path, target)


def repair_row(row, previous_rows):
    match = PRIMARY.fullmatch(row.get('slogan', ''))
    if row.get('category') or not match or row.get('publisher') not in SUBCATEGORIES:
        return False
    row['category'] = match.group(1).strip()
    row['category_rank'] = int(match.group(2))
    row['subcategory'] = row['publisher']
    row['publisher'] = row.get('change', '').strip()
    if row['publisher'] == '--':
        row['publisher'] = ''
    row['slogan'] = ''
    previous = previous_rows.get(row['name'])
    if isinstance(previous, int) and isinstance(row.get('rank'), int):
        difference = previous - row['rank']
        row['change'] = str(abs(difference)) if difference else '- 稳定'
        row['change_direction'] = 'up' if difference > 0 else 'down' if difference < 0 else 'flat'
        row['change_source'] = 'snapshot_comparison'
    else:
        row['change'] = ''
        row['change_direction'] = 'unknown'
        row['change_source'] = 'unavailable'
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted((ROOT / 'data/daily').glob('????-??-??.json'))
    snapshots = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in files}
    publisher_mode = '--publishers' in sys.argv
    publisher_restored = publisher_unavailable = 0
    affected, repaired_count, compared, unknown = [], 0, 0, 0
    for path in files:
        snapshot = snapshots[path.stem]
        before = copy.deepcopy(snapshot)
        yesterday = (date.fromisoformat(path.stem) - timedelta(days=1)).isoformat()
        previous = snapshots.get(yesterday, {})
        previous_boards = {b['label']: {r['name']: r.get('rank') for r in b.get('rows', [])}
                           for b in previous.get('platforms', {}).get('wx', {}).get('boards', [])}
        count = 0
        for board in snapshot.get('platforms', {}).get('wx', {}).get('boards', []):
            for row in board.get('rows', []):
                if publisher_mode:
                    if (re.fullmatch(r'\+\d+', row.get('publisher', ''))
                            or row.get('publisher_source') == 'official_current_page'):
                        # A current page cannot establish a historical publisher.
                        row['publisher'] = ''
                        row['publisher_source'] = 'unavailable'
                        row.pop('publisher_observed_on', None)
                        publisher_unavailable += 1
                        count += 1
                    continue
                if repair_row(row, previous_boards.get(board['label'], {})):
                    count += 1
                    compared += row['change_source'] == 'snapshot_comparison'
                    unknown += row['change_source'] == 'unavailable'
        if not count:
            continue
        assert all(before['platforms'][key] == value for key, value in snapshot['platforms'].items() if key != 'wx')
        assert [r['rank'] for b in before['platforms']['wx']['boards'] for r in b['rows']] == [r['rank'] for b in snapshot['platforms']['wx']['boards'] for r in b['rows']]
        backup(path)
        save(path, snapshot)
        affected.append(path.stem)
        repaired_count += count
    if not affected:
        print('No matching WX shift; no files changed')
        return
    latest_path = ROOT / 'data/latest.json'
    latest = json.loads(latest_path.read_text(encoding='utf-8'))
    latest_day = latest.get('date_beijing')
    if latest_day in affected:
        backup(latest_path)
        latest['platforms']['wx'] = copy.deepcopy(snapshots[latest_day]['platforms']['wx'])
        save(latest_path, latest)

    running = base._empty()
    rebuilt = []
    for day, snapshot in snapshots.items():
        diff_path = ROOT / 'data/diff' / (day + '.json')
        if day >= affected[0] and diff_path.exists():
            existing = json.loads(diff_path.read_text(encoding='utf-8'))
            before = copy.deepcopy(existing)
            wx_snapshot = {'platforms': {'wx': snapshot.get('platforms', {}).get('wx', {})}}
            classified = base.classify_today(running, wx_snapshot, day)
            for key, board in classified['boards'].items():
                merged = sorted(board['first_anywhere'] + board['first_on_board'], key=lambda r: r.get('rank') or 9999)
                board['new_to_board'] = merged
                board['totals'] = {'new_to_board': len(merged), 'returning': len(board['returning']), 'new_publishers': len(board['new_publishers'])}
                existing.setdefault('boards', {})[key] = board
            assert all(existing['boards'][key] == value for key, value in before.get('boards', {}).items() if not key.startswith('wx/'))
            backup(diff_path)
            save(diff_path, existing)
            rebuilt.append(day)
        base.absorb_snapshot(running, snapshot, day)
    for file in ('games.json', 'publishers.json'):
        backup(ROOT / 'data/base' / file)
    base.save(running)
    summary = {'days': affected, 'repaired_rows': repaired_count, 'change_recomputed_from_adjacent_snapshot': compared,
               'change_unavailable': unknown, 'rebuilt_wx_diff_days': rebuilt, 'other_platforms_unchanged': True}
    if publisher_mode:
        summary.update({'publisher_restored': publisher_restored, 'publisher_unavailable': publisher_unavailable})
    save(OUT / ('publisher-summary.json' if publisher_mode else 'summary.json'), summary)
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
