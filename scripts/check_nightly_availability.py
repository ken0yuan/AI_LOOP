#!/usr/bin/env python3
"""Check nightly availability without downloading or invalidating a prepared task."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import fetch_nightly_wheels as fetch
from obs_downloader import ObsDownloadError
import preparation_state as task_state

ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('OBS timestamps must include a timezone')
    return result


def fingerprint(selection):
    objects = dict(selection.get('objects', {}))
    source = selection.get('pytorch_source', {})
    if source.get('url'):
        objects[source['url']] = source
    return sorted((url, item.get('etag')) for url, item in objects.items())


def check(config, previous=None, *, max_age_hours=48, now=None):
    if max_age_hours <= 0:
        raise ValueError('max_age_hours must be positive')
    profile = fetch.target_profile(config)
    now = now or datetime.now(timezone.utc)
    report = {'checked_at': now.isoformat(), 'obs_prefix': config['obs_prefix'],
              'target': {key: config[key] for key in task_state.ENVIRONMENT},
              'max_age_hours': max_age_hours}
    downloader, identity, selector = fetch.load_provider(config)
    try:
        listing, source_listing = fetch.list_inputs(downloader, config)
    except ObsDownloadError as error:
        return dict(report, status='QUERY_FAILED', error=str(error),
                    message='OBS query failed; this does not establish that no new build exists.')
    py = ''.join(config['python_version'].split('.')[:2])
    matched = {url: item for url, item in listing.items()
               if url.startswith(config['obs_prefix']) and identity(url)
               and identity(url).get('python_tag') == py
               and identity(url).get('architecture') == config['architecture']
               and all(token in url.rsplit('/', 1)[-1] for token in profile['wheel_name_contains'])}
    source_prefix = fetch.pytorch_source_prefix(config)
    report['pytorch_source_obs_prefix'] = source_prefix
    sources = [(url, item) for url, item in source_listing.items()
               if re.fullmatch(re.escape(source_prefix.rstrip('/')) + r'/pytorch-source-[0-9a-f]{7,40}\.tar\.gz', url)]
    if sources:
        url, item = max(sources, key=lambda pair: timestamp(pair[1]['last_modified']))
        report['newest_source'] = dict(item, url=url)
    report.update(object_count=len(listing), target_wheel_objects=len(matched))
    missing_sources = []
    for short in sorted({identity(url)['pytorch_short'] for url in matched}):
        bundle = selector(matched, config['obs_prefix'].rstrip('/'), short,
                          {'obs': {'wheel_name_contains': []}})
        if bundle['wheels'] and source_prefix + 'pytorch-source-' + short + '.tar.gz' not in source_listing:
            missing_sources.append(short)
    report['complete_pairs_missing_source'] = missing_sources
    eligible = {url: item for url, item in matched.items()
                if source_prefix + 'pytorch-source-' + identity(url)['pytorch_short'] + '.tar.gz' in source_listing}
    if not eligible:
        return dict(report, status='NO_COMPLETE_BUNDLE',
                    message='No compatible complete wheel/source bundle; existing task inputs are unchanged.')
    try:
        selection = fetch.select_latest_pair(eligible, config, identity, selector)
    except ValueError as error:
        if not str(error).startswith('No complete paired wheels'):
            raise
        return dict(report, status='NO_COMPLETE_BUNDLE', message=str(error))
    short = next(iter(selection['identities'].values()))['pytorch_short']
    source = source_prefix + 'pytorch-source-' + short + '.tar.gz'
    selection['pytorch_source'] = dict(source_listing[source], url=source, commit=short)
    completed = max(timestamp(item['last_modified']) for item in selection['objects'].values())
    age = (now - completed).total_seconds() / 3600
    report.update(selection=selection, bundle_completed_at=completed.isoformat(),
                  age_hours=round(age, 2), stale=age > max_age_hours,
                  clock_skew=age < -0.1, status='AVAILABLE', newer_than_task=None)
    if previous:
        same = fingerprint(previous) == fingerprint(selection)
        previous_objects = previous.get('objects', {})
        previous_times = [timestamp(item['last_modified']) for item in previous_objects.values()]
        if same:
            report.update(status='NO_NEW_BUILD', newer_than_task=False)
        elif previous_times and completed >= max(previous_times):
            report.update(status='NEWER_AVAILABLE', newer_than_task=True)
        else:
            report.update(status='DIFFERENT_AVAILABLE', newer_than_task=False,
                          message='Available bundle differs but is not newer; do not downgrade the current task.')
    if report['stale']:
        report['freshness_note'] = 'The latest compatible published bundle is old; newer source archives do not imply newer wheels.'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--basic-information', type=Path)
    group.add_argument('--defaults', type=Path)
    parser.add_argument('--data-root', type=Path, default=ROOT / 'data')
    parser.add_argument('--previous-record', type=Path)
    parser.add_argument('--max-age-hours', type=float, default=48)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    protected = [ROOT / 'api-defaults.json']
    basic = None
    if args.basic_information:
        basic_path, basic = task_state.load_basic(args.basic_information, args.data_root)
        task_state.require_nightly_task(basic_path, basic)
        config = task_state.task_config(basic)
        previous_path = args.previous_record or basic_path.parent / 'log/step0-preparation.json'
        protected += [basic_path, basic_path.parent / 'log/step0-preparation.json']
    else:
        config_path = args.defaults or ROOT / 'api-defaults.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        protected.append(config_path)
        previous_path = args.previous_record
    previous = None
    if previous_path:
        protected.append(previous_path)
        if previous_path.exists():
            previous = json.loads(previous_path.read_text(encoding='utf-8')).get('selection')
    if args.output and args.output.resolve() in {p.resolve() for p in protected}:
        raise ValueError('Output must not overwrite task inputs or preparation records')
    report = check(config, previous, max_age_hours=args.max_age_hours)
    if basic:
        report['task_environment_status'] = basic.get('environment_status')
        report['task_preparation_status'] = basic.get('preparation_status')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        task_state.write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report['status'] in {'QUERY_FAILED', 'NO_COMPLETE_BUNDLE'} else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('NIGHTLY_CHECK_FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
