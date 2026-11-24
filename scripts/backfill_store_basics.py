"""One resumable US metadata pass; no AI research or Apple video requests."""
from __future__ import annotations

import copy
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from release_monitor import assess, save
from scrape_googleplay import fetch_details
from scrape_ios import lookup_apps
from store_apps import METRIC_FIELDS
from store_videos import VIDEO_FIELDS

ROOT = Path(__file__).resolve().parents[1]
DAY = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
OUT = ROOT / 'outputs' / ('basic-profile-backfill-' + DAY)
FIELDS = ('name', 'publisher', 'description', 'version', 'icon', 'screenshots', 'category',
          'supported_platforms', 'free', 'price', 'currency', 'rating', 'rating_count',
          'review_count', 'installs', 'metrics_checked_at', 'videos', 'video_checked_at',
          'media_status', 'media_missing', 'release_date', 'last_updated_date')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    store_path = ROOT / 'data/base/store-apps.json'
    catalog_path = ROOT / 'data/releases/catalog.json'
    snapshots = sorted((ROOT / 'data/releases').glob('????-??-??.json'))
    snapshot_path = snapshots[-1] if snapshots else None
    paths = [store_path, catalog_path, *([snapshot_path] if snapshot_path else [])]
    for path in paths:
        backup = OUT / ('before-' + path.name)
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
    store = json.loads(store_path.read_text(encoding='utf-8'))
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    progress_path = OUT / 'progress.json'
    progress = json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.exists() else {}
    targets = {key: copy.deepcopy(app) for key, app in store.items()
               if app.get('platform') in ('ios', 'android') and app.get('country') == 'us'}
    for record in catalog.get('apps', {}).values():
        if record.get('platform') not in ('ios', 'android') or record.get('status') == 'not_game':
            continue
        key = f"ios:us:{record['id']}" if record['platform'] == 'ios' else f"android:{record['id']}"
        targets.setdefault(key, {**record, 'country': 'us'})

    def checkpoint():
        for key, app in store.items():
            if app.get('platform') == 'ios':
                app.pop('video_error', None)
        save(store_path, store)
        for record in catalog.get('apps', {}).values():
            key = f"ios:us:{record['id']}" if record.get('platform') == 'ios' else f"android:{record['id']}"
            if progress.get(key, {}).get('status') == 'success':
                current = store[key]
                record.update({field: current[field] for field in FIELDS if field in current})
                record['media_checked_at'] = progress[key]['checked_at']
            if record.get('platform') == 'ios':
                record.pop('video_error', None)
        save(catalog_path, catalog)
        if snapshot_path:
            snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
            for record in snapshot.get('apps', []):
                current = catalog.get('apps', {}).get(f"{record['platform']}:{record['id']}")
                if current:
                    record.update({field: current[field] for field in (*FIELDS, 'media_checked_at') if field in current})
                    if record['platform'] == 'ios':
                        record.pop('video_error', None)
            snapshot['basic_media'] = {'total': len(catalog['apps']), 'complete': sum(r.get('media_status') == 'complete' for r in catalog['apps'].values())}
            save(snapshot_path, snapshot)
        save(progress_path, progress)

    def accept(key, detail):
        original = targets[key]
        now = datetime.now(timezone.utc)
        result = assess({'id': str(original['id']), 'platform': original['platform']}, detail, now)
        if result['status'] not in ('verified', 'scheduled', 'undated'):
            raise ValueError('Official result: ' + result['status'])
        assert detail is not None and str(detail.get('trackId') or detail.get('appId')) == str(original['id'])
        app = store.setdefault(key, copy.deepcopy(original))
        for field in FIELDS:
            value = result.get(field)
            if value is not None and value != '' and (value != [] or field == 'media_missing'):
                app[field] = value
        app['developer'] = result['publisher'] or app.get('developer', '')
        app['url'] = result['url']
        app['media_checked_at'] = now.isoformat()
        app['details_checked_on' if original['platform'] == 'android' else 'checked_on'] = DAY
        raw_fields = ('trackId', 'trackName', 'genreIds', 'primaryGenreId', 'releaseDate', 'currentVersionReleaseDate', 'price', 'currency', 'sellerName', 'artistName', 'artworkUrl512', 'supportedDevices') if original['platform'] == 'ios' else ('appId', 'title', 'genreId', 'released', 'updated', 'developer', 'free', 'price', 'currency', 'icon', *VIDEO_FIELDS)
        app['release_info'] = {field: detail.get(field) for field in (*raw_fields, *METRIC_FIELDS)}
        if original['platform'] == 'android':
            app['video_error'] = None
        progress[key] = {'status': 'success', 'name': app['name'], 'checked_at': now.isoformat(),
                         'missing': result['media_missing']}

    def failure(key, error):
        progress[key] = {'status': 'failed', 'name': targets[key].get('name'),
                         'error': str(error)[:500], 'checked_at': datetime.now(timezone.utc).isoformat()}

    print(f'Targets: {len(targets)}; Apple video paused', flush=True)
    for round_index in range(2):
        for platform in ('ios', 'android'):
            pending = [key for key, app in targets.items() if app['platform'] == platform and progress.get(key, {}).get('status') != 'success']
            size = 25 if platform == 'ios' else 12
            for start in range(0, len(pending), size):
                batch = pending[start:start + size]
                rate_limited = False
                if platform == 'ios':
                    try:
                        details = {str(row.get('trackId')): row for row in lookup_apps('us', [str(targets[key]['id']) for key in batch])}
                        for key in batch:
                            try:
                                accept(key, details.get(str(targets[key]['id'])))
                            except Exception as error:
                                failure(key, error)
                    except Exception as error:
                        rate_limited = '429' in str(error)
                        for key in batch:
                            failure(key, error)
                else:
                    def fetch(key):
                        try:
                            return key, fetch_details(str(targets[key]['id']), timeout=15), None
                        except Exception as error:
                            return key, None, error
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        for key, detail, error in pool.map(fetch, batch):
                            try:
                                if error:
                                    raise error
                                accept(key, detail)
                            except Exception as error:
                                rate_limited |= '429' in str(error)
                                failure(key, error)
                checkpoint()
                successes = sum(progress.get(key, {}).get('status') == 'success' for key in targets)
                print(f'Round {round_index + 1} {platform} {min(start + size, len(pending))}/{len(pending)}; total success={successes}/{len(targets)}', flush=True)
                if rate_limited:
                    print('Rate limited; stop this platform until retry round', flush=True)
                    break
                time.sleep(1)
        if round_index == 0 and any(progress.get(key, {}).get('status') != 'success' for key in targets):
            print('Retry remaining records after 30 seconds', flush=True)
            time.sleep(30)
    checkpoint()
    summary = {'targets': len(targets), 'platforms': {}, 'apple_video': 'paused', 'failed': []}
    for platform in ('ios', 'android'):
        records = [(key, app) for key, app in targets.items() if app['platform'] == platform]
        summary['platforms'][platform] = {'total': len(records), 'success': sum(progress.get(key, {}).get('status') == 'success' for key, _ in records),
            'ratings': sum('rating' in store.get(key, {}) for key, _ in records),
            'videos': sum(bool(store.get(key, {}).get('videos')) for key, _ in records),
            'complete_media': sum(store.get(key, {}).get('media_status') == 'complete' for key, _ in records)}
    summary['failed'] = [{'key': key, **progress.get(key, {'status': 'unattempted'})} for key in targets if progress.get(key, {}).get('status') != 'success']
    save(OUT / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
