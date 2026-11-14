#!/usr/bin/env python3
"""Sonarr 4 aggregate import -> one upPollo upload, with history-backed names."""
import argparse
import contextlib
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from srrdb_check import check as check_srrdb

# Only NFO and subtitle sidecars are eligible for Transit. Samples and other
# scene extras must never be copied or uploaded.
EXTRA_EXTS = {'.nfo', '.srt', '.vtt', '.idx', '.sub', '.ass', '.ssa', '.sup'}
LOG_PATH = None

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def log(message):
    line = f'{now()} {message}'
    print(line, flush=True)
    if LOG_PATH is not None:
        with open(LOG_PATH, 'a', encoding='utf-8') as stream:
            stream.write(line + '\n')

def require(test, message):
    if not test:
        raise ValueError(message)

def safe_part(part):
    require(bool(part) and not part.startswith('.') and '/' not in part and '\\' not in part
            and not any(ord(c) < 32 or ord(c) == 127 for c in part), 'Unsicherer Originalname')
    return part

def csv_set(value, delimiter=','):
    values = value.split(delimiter) if value else []
    require(values and all(v.isdigit() for v in values), 'Episoden-/Dateiliste fehlt oder ist ungültig')
    return set(values)

def data_value(record, name):
    return next((v for k, v in record.get('data', {}).items() if k.lower() == name.lower()), '')

def make_plan(event, records, expected_episode_ids=None):
    """Pure validation; no inferred video names and no count-based readiness timer."""
    download = event['sonarr_download_id']
    records = [r for r in records if r.get('downloadId') == download]
    expected = {str(r['episodeId']) for r in records if r.get('eventType') == 'grabbed'}
    if expected_episode_ids is not None:
        require(event.get('sonarr_download_client_type', '').lower() == 'usenet blackhole', 'Alternative Episodenprüfung nur für Blackhole erlaubt')
        expected = set(expected_episode_ids)
    actual = csv_set(event.get('sonarr_episodefile_episodeids', ''))
    require(expected and actual == expected, 'PENDING: Import enthält nicht exakt alle beim Grab erwarteten Episoden')
    paths = event.get('sonarr_episodefile_paths', '').split('|')
    require(paths and all(paths) and len(set(paths)) == len(paths), 'Sammelmeldung ohne eindeutige Dateipfade')
    require(len(paths) == int(event.get('sonarr_episodefile_count', '0')), 'Dateianzahl widerspricht Sammelmeldung')
    source = PurePosixPath(event.get('sonarr_sourcepath', ''))
    require(source.is_absolute(), 'Originaler Downloadpfad fehlt')
    imports = [r for r in records if r.get('eventType') == 'downloadFolderImported'
               and str(r.get('episodeId')) in actual]
    pairs = {(data_value(r, 'droppedPath'), data_value(r, 'importedPath')) for r in imports}
    require(all(a and b for a, b in pairs), 'Historie enthält leere Original-/Importpfade')
    require({b for a, b in pairs} == set(paths) and len(pairs) == len(paths),
            'Historie und Sammelmeldung lassen sich nicht eindeutig zuordnen')
    require({str(r['episodeId']) for r in imports} == actual, 'Importhistorie noch unvollständig')
    # For a single-file import SourcePath may be a file instead of a directory.
    root = source.parent if str(source) in {a for a, b in pairs} else source
    folder = safe_part(root.name)
    videos = []
    for original, imported in sorted(pairs):
        original_path = PurePosixPath(original)
        require(original_path.is_absolute(), 'Originalpfad ist relativ')
        try:
            rel = original_path.relative_to(root)
        except ValueError:
            raise ValueError('Originaldatei liegt außerhalb des Downloadordners')
        for part in rel.parts:
            safe_part(part)
        require(rel.suffix.lower() in {'.mkv', '.mp4'}, 'Nicht unterstütztes Videoformat')
        require(PurePosixPath(imported).suffix.lower() == rel.suffix.lower(), 'Video-Dateiendungen widersprechen sich')
        videos.append({'original': original, 'imported': imported, 'relative': str(rel)})
    require(len({v['relative'] for v in videos}) == len(videos), 'Kollision bei Originalnamen')
    release = event.get('sonarr_release_title', '')
    if not release and event.get('sonarr_download_client_type', '').lower() == 'usenet blackhole':
        release = folder
    require(release and not any(ord(c) < 32 for c in release), 'Release-Titel fehlt')
    return {'root': str(root), 'transitFolder': folder, 'release': release,
            'releaseType': event.get('sonarr_release_releasetype', ''),
            'episodeIds': sorted(actual), 'videos': videos}

def select_transit_layout(plan, extras):
    # Match the complete release-group suffix, independently of letter case.
    title = plan.get('release', '').casefold()
    allowed = ('snk', 'weebpinn', 'driftking', 'toonshub', 'pudding-sama')
    single = (any(len(title) > len(g) + 1 and title.endswith('-' + g) for g in allowed)
              and len(plan['videos']) == 1 and not extras
              and PurePosixPath(plan['videos'][0]['relative']).suffix.lower() == '.mkv')
    plan['transitKind'] = 'file' if single else 'directory'
    plan['transitName'] = safe_part(PurePosixPath(plan['videos'][0]['relative']).name
                                    if single else plan['transitFolder'])
    if single:
        # Only the transit-relative path changes; the original download path stays recorded.
        plan['videos'][0]['relative'] = plan['transitName']


def transit_spec(record):
    folder = safe_part(record['transitFolder'])
    kind = record.get('transitKind', 'directory')
    require(kind in {'file', 'directory'}, 'Unbekannter Transittyp')
    name = safe_part(record.get('transitName', folder))
    require((kind == 'directory' and name == folder)
            or (kind == 'file' and name.lower().endswith('.mkv')), 'Ungültiger Transitname')
    return kind, name


@contextlib.contextmanager
def transit_locks(base, record):
    _, name = transit_spec(record)
    names = sorted({record['transitFolder'], name})
    with contextlib.ExitStack() as stack:
        for value in names:
            stack.enter_context(lock(base / '_state' / (hashlib.sha256(value.encode()).hexdigest() + '.release.lock')))
        yield


def require_empty_upload_target(config, plan):
    special_group = (config.get('SPECIAL_GROUP') and plan['release'].upper().endswith('-' + config['SPECIAL_GROUP'].upper()))
    key = 'CLIENT_SPECIAL_BASE' if special_group else 'CLIENT_UPLOAD_BASE'
    base = Path(config.get(key) or ('/data/torrents/special' if special_group else '/data/torrents/upload'))
    require(base.is_absolute() and base.is_dir(), 'Upload-Zielbasis fehlt oder ist nicht eingebunden: ' + str(base))
    kind, name = transit_spec(plan)
    names = {plan['transitFolder']}
    if kind == 'file':
        names.update({name, safe_part(PurePosixPath(name).stem)})
    for value in names:
        target = base / value
        require(not target.exists() and not target.is_symlink(),
                'Upload-Ziel existiert bereits; keine Vermischung erlaubt: ' + str(target))

def collapse_duplicate_folders(plan, extras):
    """Normalize only transit paths; source paths and snapshots stay unchanged."""
    folder = plan['transitFolder']
    items = plan['videos'] + extras
    paths = []
    for item in items:
        path = PurePosixPath(item['relative'])
        require(not path.is_absolute(), 'Absoluter Transit-Dateipfad')
        parts = list(path.parts)
        for part in parts:
            safe_part(part)
        while len(parts) > 1 and parts[0] == folder:
            parts.pop(0)
        paths.append(PurePosixPath(*parts))
    require(len(set(paths)) == len(paths), 'Namenskollision nach Entfernen doppelter Release-Ordner')
    files = set(paths)
    require(not any(parent in files for path in paths for parent in path.parents),
            'Datei-/Ordnerkonflikt nach Entfernen doppelter Release-Ordner')
    changed = sum(item['relative'] != str(path) for item, path in zip(items, paths))
    for item, path in zip(items, paths):
        item['relative'] = str(path)
    return changed

def filter_plan(plan, event, filters, extras):
    texts = [event.get('sonarr_release_quality', '') + ' ' + plan['release']]
    qualities = event.get('sonarr_episodefile_qualities', '').split('|')
    paths = event.get('sonarr_episodefile_paths', '').split('|')
    quality_map = dict(zip(paths, qualities))
    texts += [quality_map.get(v['imported'], '') + ' ' + v['relative'] for v in plan['videos']]
    for text in texts:
        allowed_resolution = False
        for row in filters:
            kind, name = row[:2]
            if kind not in {'tag', 'resolution'}:
                continue
            if re.search(r'(^|[-. _/])' + re.escape(name) + r'([-. _/]|$)', text, re.I):
                require(row[2] == 'true', f'FILTER: Gesperrter {kind}: {name}')
                if kind == 'resolution':
                    allowed_resolution = True
        require(allowed_resolution, 'FILTER: Keine erlaubte Auflösung für eine Datei erkannt')
    names = [plan['release']] + [Path(v['relative']).stem for v in plan['videos']]
    for row in filters:
        kind, name = row[:2]
        if kind == 'group':
            require(not any(n.upper().endswith('-' + name.upper()) for n in names), f'FILTER: Gruppe {name} gesperrt')
        if kind == 'nfo' and any(n.upper().endswith('-' + name.upper()) for n in names):
            require(any(x['validNfo'] for x in extras), f'FILTER: Brauchbare NFO für {name} fehlt')

def write_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextlib.contextmanager
def lock(path):
    import fcntl
    with open(path, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def http_json(url, payload=None, headers=None):
    opener = urllib.request.build_opener(NoRedirect())
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    with opener.open(request, timeout=60) as response:
        return json.load(response)

def history(config, download):
    result = []
    for page in range(1, 1001):
        query = urllib.parse.urlencode({'downloadId': download, 'page': page, 'pageSize': 250})
        obj = http_json(config['SONARR_URL'].rstrip('/') + '/api/v3/history?' + query,
                        headers={'X-Api-Key': config['SONARR_API_KEY']})
        records = obj.get('records')
        require(isinstance(records, list), 'Ungültige Sonarr-Historie')
        result.extend(records)
        if len(result) >= int(obj['totalRecords']):
            return result
        require(bool(records), 'Leere Seite vor Ende der Sonarr-Historie')
    raise ValueError('Historie zu groß; manuell prüfen')

def blackhole_expected(config, event):
    series = event.get('sonarr_series_id', '')
    season = event.get('sonarr_episodefile_seasonnumber', '')
    require(series.isdigit() and season.isdigit(), 'Blackhole: Serien-/Staffel-ID fehlt')
    episodes = http_json(config['SONARR_URL'].rstrip('/') + '/api/v3/episode?' +
                        urllib.parse.urlencode({'seriesId': series}),
                        headers={'X-Api-Key': config['SONARR_API_KEY']})
    require(isinstance(episodes, list), 'Blackhole: Episodenliste ungültig')
    expected = {str(e['id']) for e in episodes if e.get('seasonNumber') == int(season)}
    actual = csv_set(event.get('sonarr_episodefile_episodeids', ''))
    if event.get('sonarr_release_releasetype') == 'SingleEpisode':
        require(len(actual) == 1 and actual <= expected, 'Blackhole: Einzelfolge nicht eindeutig zugeordnet')
        return actual
    require(event.get('sonarr_release_releasetype') == 'SeasonPack' and expected and actual == expected,
            'Blackhole: Pack enthält nicht exakt alle Episoden der Sonarr-Staffelliste')
    return expected

def check_indexer(event, config, filters):
    client = event.get('sonarr_download_client_type', '').lower()
    if client in {'sabnzbd', 'nzbget'}:
        inspection = event.get('sonarr_release_indexer', '')
        require(bool(inspection), 'Usenet-Indexer fehlt in Sammelmeldung')
    elif client == 'usenet blackhole':
        log('Blackhole: Ursprüngliche Downloadseite unbekannt; Quellenfilter entfällt. Release- und Vollständigkeitsprüfungen bleiben aktiv.')
        return
    elif client == 'qbittorrent':
        download = event['sonarr_download_id']
        require(re.fullmatch(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', download), 'Torrent-Hash ungültig')
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
        base = config['QBIT_URL'].rstrip('/')
        credentials = urllib.parse.urlencode({'username': config['QBIT_USER'], 'password': config['QBIT_PASS']}).encode()
        with opener.open(base + '/api/v2/auth/login', data=credentials, timeout=30) as resp:
            require(resp.read().strip() == b'Ok.', 'qBittorrent-Login fehlgeschlagen')
        with opener.open(base + '/api/v2/torrents/trackers?' + urllib.parse.urlencode({'hash': download}), timeout=30) as resp:
            trackers = json.load(resp)
        require(isinstance(trackers, list), 'Ungültige Tracker-Antwort')
        inspection = '\n'.join(t['url'] for t in trackers if re.match(r'^(https?|udp|wss?)://', t.get('url', ''), re.I))
        require(bool(inspection), 'Keine echten Tracker gefunden')
    else:
        raise ValueError('Nicht unterstützter Download-Client: ' + client)
    for row in filters:
        if row[0] == 'indexer':
            require(row[1].lower() not in inspection.lower(), 'FILTER: Gesperrte Quelle: ' + row[1])

def pack_capture_root(config, event, grab):
    """Use the queue's download identity, not an inferred parent directory."""
    if grab.get('sonarr_release_releasetype') != 'SeasonPack':
        return None
    download = event['sonarr_download_id']
    require(grab.get('sonarr_download_id') == download and
            grab.get('sonarr_series_id') == event.get('sonarr_series_id'), 'Pack-Zuordnung widerspricht Grab')
    roots = set()
    for page in range(1, 101):
        query = urllib.parse.urlencode({'page': page, 'pageSize': 250})
        obj = http_json(config['SONARR_URL'].rstrip('/') + '/api/v3/queue?' + query,
                        headers={'X-Api-Key': config['SONARR_API_KEY']})
        for row in obj['records']:
            if row.get('downloadId') == download and str(row.get('seriesId')) == event['sonarr_series_id']:
                if row.get('outputPath'):
                    roots.add(row['outputPath'])
        if page * 250 >= obj['totalRecords']:
            break
    else:
        raise ValueError('Queue zu groß für eindeutige Pack-Zuordnung')
    if not roots:
        return None
    require(len(roots) == 1, 'Mehrdeutiger Pack-Quellordner')
    root = Path(roots.pop())
    folder = Path(event.get('sonarr_episodefile_sourcefolder', ''))
    require(root.is_absolute() and folder.is_absolute() and
            (folder == root or root in folder.parents), 'Queue-Pfad enthält Episodenordner nicht')
    # Conservative boundary: an unexpected queue layout needs explicit inspection.
    if event.get('sonarr_download_client_type', '').lower() == 'usenet blackhole':
        base = Path(config.get('BLACKHOLE_BASE') or '/data/downloads/complete/tv')
        require(base.is_absolute() and root.parent == base, 'Blackhole: Queue-Ordner liegt nicht direkt unter BLACKHOLE_BASE')
    else:
        require(root.name == grab.get('sonarr_release_title'), 'Queue-Packordner weicht vom Grab-Namen ab')
    return root

def capture_marker(root, spool):
    return spool / (hashlib.sha256(str(Path(root)).encode()).hexdigest() + '.capture')

def capture_available(root, spool):
    marker = capture_marker(root, spool)
    if not marker.is_file():
        return False
    saved = json.loads(marker.read_text(encoding='utf-8'))
    if saved.get('root') != str(Path(root)) or saved.get('complete') is not True:
        return False
    # A marker alone is insufficient: all recorded snapshots must still be intact.
    for key in saved['files']:
        require(re.fullmatch(r'[a-f0-9]{64}', key), 'Ungültige Extras-Sicherungsreferenz')
        record = json.loads((spool / (key + '.json')).read_text(encoding='utf-8'))
        content = (spool / (key + '.extra')).read_bytes()
        require(hashlib.sha256(content).hexdigest() == record['sha256'], 'Extras-Sicherung fehlt oder wurde verändert')
    return True

def capture_extras(root, spool):
    """Persist extras while the download folder is still available; no library scan."""
    root = Path(root)
    if not root.is_dir():
        return False
    require(root.is_absolute() and not root.is_symlink() and root.resolve() == root.absolute(), 'Extras-Quellordner wird umgeleitet')
    # Invalidate an earlier success before a fresh scan, so a failed scan cannot
    # leave a stale complete marker usable at the next aggregate event.
    marker = capture_marker(root, spool)
    marker.unlink(missing_ok=True)
    files = []
    def walk_error(error):
        raise error
    for directory, dirs, names in os.walk(root, onerror=walk_error, followlinks=False):
        require(not any((Path(directory) / d).is_symlink() for d in dirs), 'Extras-Unterordner ist ein Symlink')
        files.extend(Path(directory) / name for name in names)
    captured_keys = []
    for file in files:
        if file.suffix.lower() not in EXTRA_EXTS:
            continue
        require(file.is_file() and not file.is_symlink() and file.resolve() == file.absolute(), 'Extra ist umgeleitet')
        key = hashlib.sha256(str(file).encode()).hexdigest()
        target = spool / (key + '.extra')
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        record = spool / (key + '.json')
        captured_keys.append(key)
        if record.exists():
            require(json.loads(record.read_text())['sha256'] == digest, 'Extra wurde während des Imports verändert')
            require(target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest, 'Extras-Sicherung fehlt oder wurde verändert')
            continue
        shutil.copyfile(file, target)
        target.chmod(file.stat().st_mode & 0o777)
        write_json(record, {'original': str(file), 'snapshot': str(target), 'sha256': digest})
    # Zero extras is a valid result, but only after a successful directory scan.
    write_json(marker, {'root': str(root), 'complete': True, 'files': captured_keys, 'capturedAt': now()})
    return True

def extras_for(plan, spool, imdb):
    extras = []
    root = Path(plan['root'])
    for record in sorted(spool.glob('*.json')):
        item = json.loads(record.read_text())
        try:
            rel = Path(item['original']).relative_to(root)
        except ValueError:
            continue
        for part in rel.parts:
            safe_part(part)
        item['relative'] = str(rel)
        item['validNfo'] = False
        if rel.suffix.lower() == '.nfo':
            text = Path(item['snapshot']).read_text(errors='replace')
            media_dump = (re.search(r'^(General|Allgemein)\s*$', text, re.M | re.I)
                          and re.search(r'^[A-Za-z ]+ {10,}:', text, re.M)
                          and not re.search(r'imdb\.com|themoviedb\.org|director|handlung', text, re.I))
            item['validNfo'] = bool(text.strip()) and not media_dump
            # Series packs may legitimately contain episode IMDb IDs: retain Sonarr IDs.
            if not item['validNfo']:
                continue
        extras.append(item)
    return extras

def payload_for(plan, event, dest, config):
    imdb = event.get('sonarr_series_imdbid', '')
    tmdb = event.get('sonarr_series_tmdbid', '') or '0'
    require(not imdb or re.fullmatch(r'tt\d{7,10}', imdb), 'Ungültige Serien-IMDb-ID')
    require(tmdb.isdigit(), 'Ungültige Serien-TMDb-ID')
    require(imdb or int(tmdb) > 0, 'Keine Serien-IMDb/TMDb-ID; TVDb ist kein Ersatzfeld in dieser API')
    return {'APIKey': config['API_KEY'], 'Path': str(dest), 'IMDBID': imdb,
            'TMDBID': int(tmdb), 'ForceTvShow': True,
            'Category': config.get('SPECIAL_GROUP') if (config.get('SPECIAL_GROUP') and plan['release'].upper().endswith('-' + config['SPECIAL_GROUP'].upper())) else None}

def run(config, event, args):
    global LOG_PATH
    LOG_PATH = None
    required = ['SONARR_URL', 'SONARR_API_KEY', 'UPPOLLO_URL', 'API_KEY', 'TRANSIT_BASE']
    for name in required:
        require(bool(config.get(name)), 'Konfiguration fehlt: ' + name)
    require(config.get('DRY_RUN', 'true') in {'true', 'false'}, 'DRY_RUN muss true oder false sein')
    if event.get('sonarr_eventtype') == 'Test' or args.check:
        from uppollo_connection_test import check_connection
        log(check_connection(config))
        return
    if event.get('sonarr_eventtype') not in {'Grab', 'Download'}:
        return
    download = event.get('sonarr_download_id', '')
    series_id = event.get('sonarr_series_id', '')
    require(download and series_id.isdigit(), 'Download-ID oder Serien-ID fehlt')
    base = Path(config['TRANSIT_BASE'])
    require(base.is_absolute() and str(base) != '/', 'Unsicherer Transit-Basispfad')
    base.mkdir(parents=True, exist_ok=True, mode=0o755)
    base.chmod(0o755)
    base = base.resolve()
    series_path = event.get('sonarr_series_path')
    if series_path:
        series_root = Path(series_path).resolve(strict=True)
        require(base != series_root and base not in series_root.parents and series_root not in base.parents, 'Transit und Bibliothek überlappen')
    for name in ['_state', '_logs', '_events']:
        (base / name).mkdir(exist_ok=True)
    job = hashlib.sha256(('sonarr\n' + series_id + '\n' + download).encode()).hexdigest()
    LOG_PATH = base / '_logs' / (job + '.log')
    spool = base / '_events' / job
    spool.mkdir(exist_ok=True)
    extras_spool = spool / 'extras'
    extras_spool.mkdir(exist_ok=True)
    with lock(base / '_state' / (job + '.lock')):
        write_json(spool / ('grab.json' if event['sonarr_eventtype'] == 'Grab' else 'latest-event.json'), event)
        if event['sonarr_eventtype'] == 'Grab':
            log('GRAB gespeichert: ' + job)
            return
        # Per-file callbacks only capture extras. Never submit an episode of a pack here.
        if not event.get('sonarr_episodefile_paths'):
            event_key = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            write_json(spool / ('import-' + event_key + '.json'), event)
            folder = event.get('sonarr_episodefile_sourcefolder', '')
            grab_path = spool / 'grab.json'
            is_blackhole = event.get('sonarr_download_client_type', '').lower() == 'usenet blackhole'
            if folder and (grab_path.is_file() or is_blackhole):
                grab = json.loads(grab_path.read_text(encoding='utf-8')) if grab_path.is_file() else {
                    'sonarr_download_id': download, 'sonarr_series_id': series_id,
                    'sonarr_release_releasetype': 'SeasonPack'}
                try:
                    root = pack_capture_root(config, event, grab)
                    if root is not None:
                        if capture_available(root, extras_spool) or capture_extras(root, extras_spool):
                            log('Extras: Pack-Hauptordner vollständig gesichert: ' + str(root))
                        else:
                            log('WARNING: Pack-Hauptordner bereits nicht mehr verfügbar: ' + str(root))
                except (ValueError, OSError, KeyError) as error:
                    log('WARNING: Frühe Pack-Sicherung nicht möglich: ' + str(error))
            if folder:
                if capture_extras(folder, extras_spool):
                    log('Extras: Quellordner vollständig geprüft und Sicherung bestätigt: ' + folder)
                else:
                    log('WARNING: Extras-Quellordner bereits bei Einzelmeldung nicht verfügbar: ' + folder)
            log('Einzelmeldung gespeichert; warte auf On Import Complete: ' + job)
            return
        state_path = base / '_state' / (job + '.json')
        if state_path.exists():
            log('SKIP: Auftrag besitzt bereits einen Status; kein erneuter POST: ' + job)
            return
        records = history(config, download)
        expected = blackhole_expected(config, event) if event.get('sonarr_download_client_type', '').lower() == 'usenet blackhole' else None
        plan = make_plan(event, records, expected)
        captured = capture_extras(plan['root'], extras_spool)
        secured = captured or capture_available(plan['root'], extras_spool)
        confirmed_snapshot = bool(getattr(args, 'saved_extras_confirmed', False))
        require(secured or args.no_extras_confirmed or confirmed_snapshot,
                'Originalordner nicht mehr verfügbar: Vollständigkeit der Extras unbekannt. Kein Upload. Für bestätigte extrasfreie Replay-Aufträge --no-extras-confirmed nutzen.')
        if secured and not captured:
            log('Extras: Verifizierte Sicherung aus der Einzelmeldung verwendet; Originalordner inzwischen entfernt.')
        extras = extras_for(plan, extras_spool, event.get('sonarr_series_imdbid', ''))
        if confirmed_snapshot:
            require(bool(extras), 'Bestätigte Extras-Sicherung ist leer')
            for extra in extras:
                require(hashlib.sha256(Path(extra['snapshot']).read_bytes()).hexdigest() == extra['sha256'], 'Extras-Sicherung wurde verändert')
            log('Extras: Vollständigkeit der vorhandenen Sicherung vom Betreiber für diesen Replay bestätigt.')
        filters = json.loads(config['FILTERS_JSON'])
        check_indexer(event, config, filters)
        filter_plan(plan, event, filters, extras)
        collapsed = collapse_duplicate_folders(plan, extras)
        if collapsed:
            log(f'Transit: Doppelte gleichnamige Release-Ordner bei {collapsed} Dateien entfernt.')
        select_transit_layout(plan, extras)
        kind, transit_name = transit_spec(plan)
        dest = base / transit_name
        require_empty_upload_target(config, plan)
        payload = payload_for(plan, event, dest, config)
        for video in plan['videos']:
            file = Path(video['imported'])
            require(file.is_file() and not file.is_symlink(), 'Importvideo fehlt oder ist ein Symlink')
            require(series_root in file.resolve().parents, 'Video liegt außerhalb der Serie')
            require(file.stat().st_size > 0 and file.stat().st_dev == base.stat().st_dev, 'Video leer oder auf anderem Dateisystem')
        require(len({x['relative'] for x in plan['videos'] + extras}) == len(plan['videos'] + extras), 'Namenskollision in Transit')
        files = {item['relative']: item.get('imported') or item['snapshot'] for item in plan['videos'] + extras}
        log(check_srrdb(plan['release'], transit_name if kind == 'directory' else None, files))
        plan['extras'] = extras
        write_json(spool / 'plan.json', plan)
        with transit_locks(base, plan):
            require(not dest.exists() and not dest.is_symlink(), 'Transitpfad existiert bereits; wird nicht verändert')
            if kind == 'file':
                legacy = base / plan['transitFolder']
                require(not legacy.exists() and not legacy.is_symlink(), 'Alter Release-Transit existiert noch; keine parallele Dateiübergabe')
                log('Transit: Einzelne MKV ohne Extras wird direkt übergeben: ' + transit_name)
            log(f"PLAN OK: {plan['releaseType']}; {len(plan['videos'])} Videos; {len(extras)} Extras; Job={job}; Path={dest}")
            if config.get('DRY_RUN', 'true') == 'true' or args.dry_run:
                log('DRY RUN OK: Kein Video-Transit und kein Upload; Plan/Extras-Sicherung gespeichert.')
                return
            state = {'application': 'sonarr', 'release': plan['release'], 'transitFolder': plan['transitFolder'],
                     'transitKind': kind, 'transitName': transit_name,
                     'downloadId': download, 'path': str(dest), 'submittedAt': '', 'source': plan['root']}
            def status(value):
                state.update(status=value, updatedAt=now())
                write_json(state_path, state)
            status('preparing')
            if kind == 'directory':
                dest.mkdir(mode=0o755)
                dest.chmod(0o755)
            for item in plan['videos'] + extras:
                original = Path(item.get('imported') or item['snapshot'])
                target = dest if kind == 'file' else dest / item['relative']
                if kind == 'directory':
                    target.parent.mkdir(parents=True, exist_ok=True)
                    parent = target.parent
                    while parent != dest:
                        parent.chmod(0o755)
                        parent = parent.parent
                os.link(original, target)
                require(os.path.samefile(original, target), 'Hardlink-Verifizierung fehlgeschlagen')
            require_empty_upload_target(config, plan)
            state['submittedAt'] = now()
            status('submitting')
            try:
                response = http_json(config['UPPOLLO_URL'], payload)
            except urllib.error.HTTPError as exc:
                status('http_error')
                raise ValueError(f'upPollo HTTP {exc.code}; Transit bleibt erhalten') from None
            except Exception:
                status('outcome_unknown')
                raise ValueError('upPollo-Antwort unklar; kein automatischer Wiederholungsversuch') from None
            if not isinstance(response, dict) or response.get('status') != 'queued':
                status('outcome_unknown')
                raise ValueError('upPollo hat queued nicht bestätigt; Transit bleibt erhalten')
            status('accepted_unverified')
            log('QUEUED: Ein Auftrag an upPollo übergeben. Transit bleibt bis zum geprüften Cleanup erhalten. Job=' + job)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--no-extras-confirmed', action='store_true')
    parser.add_argument('--saved-extras-confirmed', action='store_true')
    args = parser.parse_args()
    require(not args.saved_extras_confirmed or args.replay, '--saved-extras-confirmed ist nur für Replay erlaubt')
    require(not (args.saved_extras_confirmed and args.no_extras_confirmed), 'Extras-Bestätigungen widersprechen sich')
    require(not args.no_extras_confirmed or args.replay, '--no-extras-confirmed ist nur für gezielte Replay-Aufträge erlaubt')
    event = json.loads(args.replay.read_text(encoding='utf-8-sig')) if args.replay else {k: v for k, v in os.environ.items() if k.startswith('sonarr_')}
    run(dict(os.environ), event, args)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HTTP errors may contain URLs; never print response bodies or request payloads.
        log('ERROR: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + ': Verarbeitung abgebrochen'))
        sys.exit(1)
