#!/usr/bin/env python3
"""Clean a Sonarr transit only after an unambiguous completed dupe skip."""
import argparse
from datetime import datetime
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import urllib.parse
import urllib.request

import cleanup_support as s

LOG_LINE = re.compile(r'^time=(\S+) level=\w+ msg=("(?:[^"\\]|\\.)*")$')
DUPE_SKIP = 'RocketHD: skipped · dupe found'
TERMINAL_ERROR = 'Error during upload processing: all tracker uploads failed'
TERMINAL_SUCCESS = 'Upload processed successfully'


def seconds(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    s.require(parsed.tzinfo is not None, 'Zeitstempel ohne Zeitzone')
    return parsed.timestamp()


def parse_attempts(lines):
    """Keep starts, including incomplete/failed attempts, to detect path reuse."""
    attempts, active = [], []
    for raw in lines:
        line = raw.rstrip('\r\n')
        match = LOG_LINE.fullmatch(line)
        if not match:
            if 'msg=' in line:
                for attempt in active:
                    attempt['unsafe'] = True
            continue
        timestamp, encoded = match.groups()
        try:
            moment = seconds(timestamp)
            message = json.loads(encoded)
        except (ValueError, TypeError):
            for attempt in active:
                attempt['unsafe'] = True
            continue
        if message.startswith('New Upload: '):
            path = message[len('New Upload: '):]
            unsafe = bool(active) or not path.startswith('/') or any(ord(c) < 32 for c in path)
            for attempt in active:
                attempt['unsafe'] = True
            attempt = dict(path=path, start=timestamp, startTime=moment, finish=None,
                           outcome='pending', unsafe=unsafe, found=False, skipped=False,
                           otherTracker=False, lastTime=moment)
            attempts.append(attempt)
            active.append(attempt)
            continue
        for attempt in active:
            if moment < attempt['lastTime']:
                attempt['unsafe'] = True
            attempt['lastTime'] = moment
            if message.startswith('found dupe: '):
                attempt['found'] = True
            if message == DUPE_SKIP:
                attempt['skipped'] = True
            # Do not turn mixed tracker failures or non-dupe skips into dupe cleanup.
            if re.match(r'^[^:]+: (skipped|failed|uploaded|success|pending)\b', message) and message != DUPE_SKIP:
                attempt['otherTracker'] = True
        if message == TERMINAL_SUCCESS or message.startswith('Error during upload processing:'):
            if len(active) == 1:
                attempt = active[0]
                attempt['finish'] = timestamp
                attempt['finishTime'] = moment
                attempt['outcome'] = 'other'
                if (message == TERMINAL_ERROR and not attempt['unsafe']
                        and attempt['found'] and attempt['skipped'] and not attempt['otherTracker']):
                    attempt['outcome'] = 'duplicate'
            if active:
                active.pop(0)
    return attempts


def match_attempt(state, attempts, before):
    submitted = seconds(state['submittedAt'])
    accepted = seconds(state.get('dupeAcceptedAt') or state['updatedAt'])
    # Any second start for this path after submission blocks deletion, even if
    # that attempt ended with another error or has not finished yet.
    candidates = [a for a in attempts if a['path'] == state['path'] and a['startTime'] >= submitted]
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if (candidate['outcome'] != 'duplicate' or candidate['startTime'] > accepted + 5
            or candidate['finishTime'] >= before):
        return None
    return {key: candidate[key] for key in ('path', 'start', 'finish')}


def plain(path, directory=False):
    mode = path.lstat().st_mode
    s.require((stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode))
              and path.resolve(strict=True) == path, 'Pfad ist umgeleitet oder hat einen falschen Typ: ' + str(path))


def read_json(path):
    plain(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    s.require(isinstance(value, dict), 'Ungültige Metadaten: ' + str(path))
    return value


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def qbit_get(config, endpoint, params=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(s.NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
    base = config['QBIT_URL'].rstrip('/')
    credentials = urllib.parse.urlencode({'username': config['QBIT_USER'], 'password': config['QBIT_PASS']}).encode()
    with opener.open(base + '/api/v2/auth/login', data=credentials, timeout=30) as response:
        s.require(response.read().strip() == b'Ok.', 'qBittorrent-Login fehlgeschlagen')
    url = base + '/api/v2/torrents/' + endpoint
    if params:
        url += '?' + urllib.parse.urlencode(params)
    with opener.open(url, timeout=30) as response:
        return json.load(response)


def qbit_info(config):
    return qbit_get(config, 'info')


def qbit_files(config, torrent_hash):
    return qbit_get(config, 'files', {'hash': torrent_hash})


def no_torrent_uses(dest, torrents):
    s.require(isinstance(torrents, list), 'Ungültige qBittorrent-Antwort')
    for torrent in torrents:
        s.require(isinstance(torrent, dict), 'Ungültiger qBittorrent-Eintrag')
        for field in ('content_path', 'save_path'):
            value = torrent.get(field)
            s.require(isinstance(value, str) and (PurePosixPath(value).is_absolute() or Path(value).is_absolute()), 'qBittorrent-Pfad fehlt')
            path = Path(value)
            s.require(path != dest and dest not in path.parents
                      and (field != 'content_path' or path not in dest.parents),
                      'Ein Torrent verwendet noch den Transit; keine Dupe-Bereinigung.')


def inventory(dest):
    files, directories = set(), []
    plain(dest, directory=True)

    def onerror(error):
        raise error

    for root, dirs, names in os.walk(dest, followlinks=False, onerror=onerror):
        directories.append(Path(root))
        for name in dirs:
            plain(Path(root) / name, directory=True)
        for name in names:
            path = Path(root) / name
            plain(path)
            files.add(path.relative_to(dest).as_posix())
    return files, directories


def verified_pairs(base, job, state, dest):
    spool = base / '_events' / job
    plain(spool, directory=True)
    plan = read_json(spool / 'plan.json')
    event = read_json(spool / 'latest-event.json')
    kind, name = s.transit_spec(state)
    s.require(s.transit_spec(plan) == (kind, name)
              and plan['transitFolder'] == state['transitFolder'] and dest.name == name
              and plan['release'] == state['release']
              and plan['root'] == state['source'], 'Plan gehört nicht zum Auftrag')
    s.require(event['sonarr_download_id'] == state['downloadId'], 'Download-ID widerspricht Ereignis')
    library = Path(event['sonarr_series_path'])
    plain(library, directory=True)
    s.require(library != base and library not in base.parents and base not in library.parents,
              'Bibliothek und Transit überlappen')
    videos, extras = plan['videos'], plan.get('extras', [])
    s.require(isinstance(videos, list) and videos and isinstance(extras, list), 'Dateiliste fehlt')
    if kind == 'file':
        s.require(len(videos) == 1 and not extras and videos[0]['relative'] == name,
                  'Dateitransit enthält keine eindeutige extrasfreie MKV')
    s.require({v['imported'] for v in videos} == set(event['sonarr_episodefile_paths'].split('|')),
              'Videos widersprechen dem Sonarr-Importereignis')
    pairs = {}
    for item, is_extra in [(v, False) for v in videos] + [(e, True) for e in extras]:
        rel = PurePosixPath(item['relative'])
        s.require(not rel.is_absolute() and str(rel) == item['relative'] and bool(rel.parts), 'Ungültiger Transitpfad')
        for part in rel.parts:
            s.safe_part(part)
        s.require(str(rel) not in pairs, 'Doppelter Transitpfad im Plan')
        source = Path(item['snapshot'] if is_extra else item['imported'])
        plain(source)
        if is_extra:
            s.require(spool / 'extras' in source.parents, 'Extras-Sicherung liegt außerhalb des Auftrags')
            s.require(re.fullmatch('[a-f0-9]{64}', item['sha256']) is not None
                      and digest(source) == item['sha256'], 'Extras-Sicherung stimmt nicht mit ihrem Hash überein')
        else:
            s.require(library in source.parents and source.stat().st_size > 0, 'Vergleichsvideo liegt nicht in der Bibliothek')
        pairs[str(rel)] = source
    return pairs


def verify_pair(target, source):
    plain(target)
    plain(source)
    s.require(target.stat().st_size == source.stat().st_size
              and (os.path.samefile(target, source) or digest(target) == digest(source)),
              'Transitdatei stimmt nicht mit ihrer erhaltenen Vergleichsdatei überein: ' + target.name)


def cleanup_job(config, job, attempts, before, apply=False):
    s.require(re.fullmatch('[a-f0-9]{64}', job) is not None, 'Ungültige Job-ID')
    base = Path(config['TRANSIT_BASE'])
    s.require(base.is_absolute() and base != base.parent, 'Ungültige Transitbasis')
    plain(base, directory=True)
    state_dir = base / '_state'
    plain(state_dir, directory=True)
    state_path = state_dir / (job + '.json')
    with s.lock(state_dir / (job + '.lock')):
        state = read_json(state_path)
        if state.get('status') == 'skipped_duplicate':
            return False
        s.require(state.get('status') in {'accepted_unverified', 'dupe_cleanup_started'}, 'Auftrag erlaubt keine Dupe-Bereinigung')
        proof = match_attempt(state, attempts, before)
        s.require(proof is not None, 'Kein eindeutiger abgeschlossener Dupe-Abbruch für diesen Auftrag')
        folder = s.safe_part(state['transitFolder'])
        kind, name = s.transit_spec(state)
        dest = base / name
        s.require(state.get('application') == 'sonarr' and state['path'] == str(dest), 'Statuspfad widerspricht Sonarr-Transit')
        with s.transit_locks(base, state):
            for other_path in state_dir.glob('*.json'):
                if other_path == state_path or not re.fullmatch('[a-f0-9]{64}', other_path.stem):
                    continue
                other = read_json(other_path)
                if other.get('path') == str(dest):
                    s.require(other.get('status') in {'cleaned', 'skipped_duplicate', 'superseded', 'archived'},
                              'Ein weiterer offener Auftrag verwendet denselben Transit')
            pairs = verified_pairs(base, job, state, dest)
            resume = state['status'] == 'dupe_cleanup_started'
            exists = os.path.lexists(dest)
            s.require(exists or resume, 'Transitordner fehlt')
            if exists and kind == 'file':
                plain(dest)
                actual, directories = {name}, []
            else:
                actual, directories = inventory(dest) if exists else (set(), [])
            s.require(actual <= pairs.keys() and (resume or actual == pairs.keys()),
                      'Transit enthält fehlende oder ungeplante Dateien')
            for relative in actual:
                verify_pair(dest if kind == 'file' else dest / relative, pairs[relative])
            no_torrent_uses(dest, qbit_info(config))
            print(f'DUPE CHECK OK: {folder}; {len(actual)} Transitdateien verifiziert.', flush=True)
            if not apply:
                print('Nichts gelöscht. Automatische Anwendung mit --apply.', flush=True)
                return True
            # Keep the original acceptance window when resuming an interrupted unlink.
            state.setdefault('dupeAcceptedAt', state['updatedAt'])
            state.update(status='dupe_cleanup_started', duplicateEvidence=proof,
                         updatedAt=s.now())
            s.write_json(state_path, state)
            no_torrent_uses(dest, qbit_info(config))
            for relative in sorted(actual):
                target = dest if kind == 'file' else dest / relative
                verify_pair(target, pairs[relative])
                target.unlink()
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                directory.rmdir()
            state.update(status='skipped_duplicate', cleanupUpdatedAt=s.now(), updatedAt=s.now())
            s.write_json(state_path, state)
            print(f'DUPE CLEANED: {folder}; Transit entfernt, Bibliothek und Extras-Sicherung erhalten.', flush=True)
            return True


def run(config, lines, before, apply=False):
    attempts = parse_attempts(lines)
    state_dir = Path(config['TRANSIT_BASE']) / '_state'
    plain(state_dir, directory=True)
    found = errors = 0
    for path in sorted(state_dir.glob('*.json')):
        if not re.fullmatch('[a-f0-9]{64}', path.stem):
            continue
        try:
            state = read_json(path)
            if state.get('status') not in {'accepted_unverified', 'dupe_cleanup_started'}:
                continue
            if match_attempt(state, attempts, before) is None:
                continue
            if cleanup_job(config, path.stem, attempts, before, apply):
                found += 1
        except Exception as error:
            # Never echo API errors/URLs, request headers or credentials.
            detail = s.error_detail(error)
            print(f'DUPE ERROR: {path.stem}: {detail}', flush=True)
            errors += 1
    print(f'Dupe-Prüfung: {found} bestätigte Aufträge; {errors} Fehler.', flush=True)
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--before', type=float, required=True)
    args = parser.parse_args()
    return run(dict(os.environ), sys.stdin, args.before, args.apply)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        detail = s.error_detail(error)
        raise SystemExit('DUPE ERROR: ' + detail) from None
