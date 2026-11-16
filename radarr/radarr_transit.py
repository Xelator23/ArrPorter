"""Radarr transit inventory, target guard and conservative cleanup. Python stdlib only."""
import contextlib
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.parse
import urllib.request

GROUPS = {'snk', 'weebpinn', 'driftking'}
SUBS = {'.srt', '.vtt', '.idx', '.sub', '.ass', '.ssa', '.sup'}
DONE = {'cleaned', 'skipped_duplicate'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def safe_rel(value):
    require(isinstance(value, str) and value and not any(ord(c) < 32 for c in value), 'Ungültiger Dateiname')
    p = PurePosixPath(value)
    require(not p.is_absolute() and '\\' not in value and all(x not in ('', '.', '..') and not x.startswith('.') for x in value.split('/')), 'Unsicherer relativer Pfad')
    return p

def exact(path, file=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), f'Pfad wird umgeleitet: {path}')
    if file:
        require(path.is_file(), f'Datei fehlt: {path}')
    return path

def contained(path, base):
    path = exact(path)
    require(path != base and path.is_relative_to(base), f'Pfad außerhalb der Basis: {path}')
    return path

def read(path):
    return json.loads(exact(path, True).read_text(encoding='utf-8'))

def write(path, obj):
    exact(path)
    tmp = path.with_name(path.name + '.new')
    with open(tmp, 'x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)

@contextlib.contextmanager
def locked(path):
    import fcntl  # Linux container; pure planning helpers also work on Windows.
    exact(path)
    with open(path, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def target_base(release):
    key = 'CLIENT_SPECIAL_BASE' if (os.environ.get('SPECIAL_GROUP') and release.upper().endswith('-' + os.environ['SPECIAL_GROUP'].upper())) else 'CLIENT_UPLOAD_BASE'
    default = '/data/torrents/special' if key == 'CLIENT_SPECIAL_BASE' else '/data/torrents/upload'
    return exact(os.environ.get(key) or default)

def candidates(s):
    base = target_base(s['release'])
    transit = Path(s['path'])
    require(base.is_dir() and base.parent != base, f'Zielbasis fehlt: {base}')
    require(not base.is_relative_to(transit.parent) and not transit.parent.is_relative_to(base), 'Ziel und Transit überlappen')
    if s['transitKind'] == 'file':
        return [base / transit.name, base / s['transitFolder'] / transit.name, base / transit.stem / transit.name]
    return [base / transit.name]

def guard(s):
    paths = candidates(s)
    base = target_base(s['release'])
    # Check entire possible destination directories, including old nested content.
    roots = {base / p.relative_to(base).parts[0] for p in paths}
    roots.add(base / s['transitFolder'])
    for path in roots:
        require(not path.exists() and not path.is_symlink(), f'Upload-Ziel existiert bereits: {path}')

def collapse(rel, folder):
    parts = list(safe_rel(rel).parts)
    while len(parts) > 1 and parts[0] == folder:
        parts.pop(0)
    return '/'.join(parts)

def plan(base, movie, video, name, folder, release, job, manifest, nfo='', extras_root=None):
    safe_rel(name); safe_rel(folder)
    require('/' not in name and '/' not in folder, 'Datei-/Ordnername enthält Pfad')
    require(re.fullmatch('[a-f0-9]{64}', job), 'Ungültige Job-ID')
    exact(base); exact(movie); exact(video, True)
    contained(video, movie)
    require(not base.is_relative_to(movie) and not movie.is_relative_to(base), 'Bibliothek und Transit überlappen')
    files = [dict(relative=name, source=str(video), kind='video', size=video.stat().st_size)]
    extras_root = exact(extras_root) if extras_root else movie
    require(manifest['destination'] == str(extras_root), 'Falsches Extras-Manifest')
    for item in manifest['files']:
        src = contained(Path(item['path']), extras_root)
        exact(src, True)
        require(digest(src) == item['sha256'], 'Extras wurden verändert')
        if str(src) != nfo and src.suffix.lower() not in SUBS:
            continue
        rel = collapse(src.relative_to(extras_root).as_posix(), folder)
        if src.suffix.lower() in SUBS:
            parts = rel.split('/')
            rel = 'Subs/' + '/'.join(parts[1:] if parts[0].lower() == 'subs' else parts)
        files.append(dict(relative=rel, source=str(src), kind='extra', size=src.stat().st_size, sha256=item['sha256']))
    names = [x['relative'] for x in files]
    require(len(set(names)) == len(names), 'Namenskollision nach Ordner-Normalisierung')
    require(not any(a != b and b.startswith(a + '/') for a in names for b in names), 'Datei-/Ordnerkollision')
    prefix, sep, group = release.rpartition('-')
    direct = bool(prefix and sep and group.casefold() in GROUPS and name.lower().endswith('.mkv') and len(files) == 1)
    dest = base / (name if direct else folder)
    require(not dest.exists() and not dest.is_symlink(), f'Transit existiert bereits: {dest}')
    return dict(manifestVersion=1, status='preparing', path=str(dest), source=str(video), moviePath=str(movie),
                release=release, transitFolder=folder, transitVideoName=name, transitKind='file' if direct else 'directory',
                job=job, files=files, submittedAt='', updatedAt=now())

def stage(base, s):
    guard(s)
    dest = exact(s['path'])
    require(not dest.exists() and not dest.is_symlink(), 'Transit existiert bereits')
    require(Path(s['source']).stat().st_dev == base.stat().st_dev, 'Video und Transit liegen auf verschiedenen Dateisystemen')
    state = base / '_state' / (s['job'] + '.json')
    require(not state.exists(), 'Auftrag besitzt bereits einen Status')
    backup = base / '_events' / s['job'] / 'extras'
    exact(backup)
    for i, item in enumerate(s['files']):
        if item['kind'] == 'extra':
            backup.mkdir(parents=True, exist_ok=True)
            snap = backup / f'{i:04d}.extra'
            with open(item['source'], 'rb') as src, open(snap, 'xb') as out:
                shutil.copyfileobj(src, out)
            require(digest(snap) == item['sha256'], 'Extras-Sicherung stimmt nicht überein')
            item['snapshot'] = str(snap)
    write(state, s)
    if s['transitKind'] == 'directory':
        dest.mkdir()
    for item in s['files']:
        target = dest if s['transitKind'] == 'file' else dest / item['relative']
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(item.get('snapshot', item['source']), target)

def inventory(base, s, partial=False):
    require(s.get('manifestVersion') == 1 and re.fullmatch('[a-f0-9]{64}', s.get('job', '')), 'Kein verifiziertes neues Dateimanifest; Altauftrag bleibt erhalten')
    require(s['transitKind'] in ('file', 'directory'), 'Ungültige Transitart')
    safe_rel(s['transitFolder']); safe_rel(s['transitVideoName'])
    require('/' not in s['transitFolder'] and '/' not in s['transitVideoName'], 'Ungültiger Transitname')
    dest = contained(Path(s['path']), base)
    require(dest.parent == base and dest.name == (s['transitVideoName'] if s['transitKind'] == 'file' else s['transitFolder']), 'Transitpfad passt nicht zum Manifest')
    require(s['files'] and (s['transitKind'] != 'file' or len(s['files']) == 1), 'Ungültige Dateiliste')
    expected = {}
    for item in s['files']:
        rel = str(safe_rel(item['relative']))
        path = dest if s['transitKind'] == 'file' else dest / rel
        require(path not in expected, 'Doppelte Manifestdatei')
        exact(path)
        if item['kind'] == 'video':
            src = contained(Path(item['source']), exact(s['moviePath']))
        else:
            require(item['kind'] == 'extra', 'Unbekannter Dateityp')
            src = contained(Path(item['snapshot']), base / '_events' / s['job'] / 'extras')
            require(digest(exact(src, True)) == item['sha256'], 'Extras-Sicherung verändert')
        exact(src, True)
        require(src.stat().st_size == item['size'], 'Sicherungs-/Bibliotheksgröße geändert')
        if path.exists():
            exact(path, True)
            require(path.stat().st_size == item['size'] and (os.path.samefile(src, path) or digest(src) == digest(path)), f'Transitinhalt verändert: {path}')
        else:
            require(partial, f'Transitdatei fehlt: {path}')
        expected[path] = src
    actual = set()
    if dest.exists():
        if s['transitKind'] == 'file':
            actual.add(exact(dest, True))
        else:
            require(dest.is_dir(), 'Transit ist kein Ordner')
            for root, dirs, names in os.walk(dest, followlinks=False):
                for name in dirs + names:
                    p = exact(Path(root) / name)
                    require(p.is_dir() or p.is_file(), 'Spezialdatei im Transit')
                    if p.is_file():
                        actual.add(p)
    require(actual <= expected.keys() and (partial or actual == expected.keys()), 'Unerwartete oder fehlende Transitdateien')
    return expected

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('HTTP-Weiterleitung wird nicht akzeptiert')

class Qbit:
    def __init__(self):
        self.url = os.environ['QBIT_URL'].rstrip('/')
        require(urllib.parse.urlparse(self.url).scheme in ('http', 'https'), 'Ungültige qBittorrent-URL')
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data = urllib.parse.urlencode(dict(username=os.environ['QBIT_USER'], password=os.environ['QBIT_PASS'])).encode()
        require(self.get('/auth/login', data, False).strip() == 'Ok.', 'qBittorrent-Login fehlgeschlagen')

    def get(self, endpoint, data=None, decode=True):
        with self.opener.open(self.url + '/api/v2' + endpoint, data=data, timeout=30) as response:
            body = response.read().decode('utf-8')
        return json.loads(body) if decode else body

def free_transit(qbit, dest):
    torrents = qbit.get('/torrents/info')
    require(isinstance(torrents, list), 'Ungültige Torrentliste')
    for torrent in torrents:
        content = Path(torrent.get('content_path') or '/')
        save = Path(torrent.get('save_path') or '/')
        require(not content.is_relative_to(dest) and not save.is_relative_to(dest), 'qBittorrent verwendet den Transit')
        # With direct files, save_path alone can be the shared transit base.
        require(not dest.is_relative_to(save) or not save.is_relative_to(dest.parent), 'qBittorrent speichert im Transitbereich')
    return torrents

def verify_target(qbit, s, expected):
    options = candidates(s)
    torrents = free_transit(qbit, Path(s['path']))
    matches = []
    for t in torrents:
        content = Path(t.get('content_path') or '/')
        for root in options:
            acceptable = content == root or (s['transitKind'] == 'directory' and len(s['files']) == 1 and content == root / s['files'][0]['relative'])
            if acceptable:
                matches.append((t, root))
    # Multiple equivalent candidate paths for a direct file must not duplicate a match.
    matches = list({(t.get('hash'), str(root)): (t, root) for t, root in matches}.values())
    require(len(matches) == 1, 'Keine eindeutige Torrent-Zuordnung')
    t, root = matches[0]
    require(t.get('progress') == 1 and t.get('amount_left') == 0 and t.get('state') in {'uploading', 'stalledUP', 'forcedUP', 'queuedUP', 'pausedUP', 'stoppedUP'}, 'Zieltorrent fehlt, ist unvollständig oder wird geprüft/verschoben')
    h = t.get('hash', '')
    require(re.fullmatch('[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', h), 'Ungültiger Torrent-Hash')
    files = qbit.get('/torrents/files?' + urllib.parse.urlencode({'hash': h}))
    require(isinstance(files, list) and files and all(f.get('progress') == 1 for f in files), 'Torrentdateien unvollständig')
    available = {}
    for f in files:
        p = exact(Path(t['save_path']) / str(safe_rel(f['name'])), True)
        require(p not in available, 'Doppelte Torrent-Dateizuordnung')
        available[p] = f['size']
    for item, src in zip(s['files'], expected.values()):
        target = root if s['transitKind'] == 'file' else root / item['relative']
        exact(target, True)
        require(available.get(target) == item['size'] == target.stat().st_size, 'Zieldatei nicht vollständig im Torrent nachgewiesen')
        require(os.path.samefile(src, target) or digest(src) == digest(target), f'Zieldateiinhalt weicht ab: {target}')
    return h

def supports_cleanup(s, job):
    """Legacy states have neither an embedded job ID nor a durable inventory."""
    if s.get('manifestVersion') is None:
        require(s.get('job') in (None, job), 'Falsche Job-Zuordnung')
        return False
    require(s.get('manifestVersion') == 1, 'Unbekannte Manifest-Version')
    require(s.get('job') == job, 'Falsche Job-Zuordnung')
    return True

def cleanup(base, job, apply=False, duplicate=False, qbit=None):
    require(re.fullmatch('[a-f0-9]{64}', job), 'Ungültige Job-ID')
    file = base / '_state' / (job + '.json')
    with locked(file.with_suffix('.lock')):
        s = read(file)
        require(supports_cleanup(s, job), 'Altstatus ohne dauerhaftes Dateimanifest: manuelle Prüfung erforderlich; keine Dateien verändert')
        if s['status'] in DONE:
            print('Bereits bereinigt.'); return
        pending = 'duplicate_cleanup_started' if duplicate else 'cleanup_started'
        require(s['status'] in ('accepted_unverified', pending), 'Status erlaubt keinen Cleanup')
        lock = hashlib.sha256(s['transitFolder'].encode()).hexdigest() + '.release.lock'
        with locked(base / '_state' / lock):
            for other in (base / '_state').glob('*.json'):
                if other != file:
                    obj = read(other)
                    require(obj.get('path') != s['path'] or obj.get('status') in (DONE | {'archived'}), 'Ein anderer offener Auftrag verwendet denselben Transit')
            expected = inventory(base, s, partial=s['status'] == pending)
            qbit = qbit or Qbit()
            free_transit(qbit, Path(s['path']))
            h = None if duplicate else verify_target(qbit, s, expected)
            print(('DUPE ' if duplicate else '') + f'CHECK OK: {s["release"]}; {len(expected)} Dateien verifiziert.')
            if not apply:
                print('Nichts gelöscht.'); return
            expected = inventory(base, s, partial=s['status'] == pending)
            free_transit(qbit, Path(s['path']))
            if not duplicate:
                require(verify_target(qbit, s, expected) == h, 'Torrent-Zuordnung geändert')
            s.update(status=pending, cleanupUpdatedAt=now())
            if h:
                s['targetTorrentHash'] = h
            write(file, s)
            for path in expected:
                if path.exists():
                    exact(path, True).unlink()
            dest = Path(s['path'])
            if s['transitKind'] == 'directory' and dest.exists():
                for root, dirs, names in os.walk(dest, topdown=False):
                    exact(root).rmdir()
            s.update(status='skipped_duplicate' if duplicate else 'cleaned', cleanupUpdatedAt=now())
            write(file, s)
            print(f'CLEANED: Nur verifizierten Transit entfernt: {dest}')

def epoch(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()

def dupe_events(lines):
    active = []
    results = []
    starts = []
    for line in lines:
        match = re.search(r'^time=(\S+).*? msg=("(?:[^"\\]|\\.)*")\s*$', line.strip())
        if not match:
            # Unknown/truncated log records during an upload prevent proof.
            for a in active:
                a['bad'] = True
            continue
        try:
            stamp, msg = epoch(match[1]), json.loads(match[2])
        except (ValueError, TypeError):
            for a in active:
                a['bad'] = True
            continue
        if msg.startswith('New Upload: '):
            for a in active:
                a['bad'] = True
            path = msg[len('New Upload: '):]
            starts.append((path, stamp))
            active.append(dict(path=path, start=stamp, bad=bool(active), found=False, skip=False))
        elif active:
            a = active[0]
            if msg.startswith('found dupe: '):
                a['found'] = True
            elif msg == 'RocketHD: skipped · dupe found':
                a['skip'] = True
            elif re.match(r'\w+: (?:uploaded|skipped|failed)', msg):
                a['bad'] = True
            if msg.startswith('Error during upload processing:') or msg == 'Upload processed successfully':
                if len(active) == 1 and not a['bad'] and a['found'] and a['skip'] and msg == 'Error during upload processing: all tracker uploads failed' and stamp >= a['start']:
                    results.append(dict(a, finish=stamp))
                active.pop(0)
    return results, starts

def dupe_matches(s, events, starts, before):
    submitted, accepted = epoch(s['submittedAt']), epoch(s['updatedAt'])
    matched = [e for e in events if e['path'] == s['path'] and submitted <= e['start'] <= accepted + 5 and e['finish'] < before]
    relevant = [t for p, t in starts if p == s['path'] and t >= submitted]
    return len(matched) == 1 and len(relevant) == 1

def main():
    mode = sys.argv[1]
    base = exact(os.environ['TRANSIT_BASE'])
    if mode == 'prepare':
        e = os.environ
        preview = e.get('RADARR_EXTRAS_PREVIEW')
        movie = Path(e['RT_BASE'])
        s = plan(base, movie, Path(e['RT_VIDEO']), e['RT_VIDEO_NAME'], e['RT_FOLDER'], e['RT_RELEASE'], e['RT_JOB'], read(e['RADARR_EXTRAS_MANIFEST']), e.get('RT_NFO', ''), preview)
        s['downloadId'] = e['RT_HASH']
        guard(s)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from srrdb_check import check
        files = {item['relative']: item['source'] for item in s['files']}
        folder = s['transitFolder'] if s['transitKind'] == 'directory' else None
        print(check(s['release'], folder, files), file=sys.stderr)
        if e['RT_DRY_RUN'] != 'true':
            if preview:
                from radarr_extras import sync
                with contextlib.redirect_stdout(sys.stderr):
                    sync(preview, movie, e['RADARR_EXTRAS_MANIFEST'], e['RT_VIDEO'])
                for item in s['files']:
                    if item['kind'] == 'extra':
                        item['source'] = str(movie / Path(item['source']).relative_to(preview))
            stage(base, s)
        print(json.dumps(s))
    elif mode == 'guard':
        guard(read(base / '_state' / (sys.argv[2] + '.json')))
    elif mode == 'cleanup':
        cleanup(base, sys.argv[3], apply=sys.argv[2] == '--apply-confirmed')
    elif mode == 'duplicates':
        events, starts = dupe_events(sys.stdin)
        errors = found = manual = 0
        for file in (base / '_state').glob('*.json'):
            try:
                s = read(file)
                if s.get('status') not in ('accepted_unverified', 'duplicate_cleanup_started') or not s.get('submittedAt'):
                    continue
                if dupe_matches(s, events, starts, float(sys.argv[3])):
                    if not supports_cleanup(s, file.stem):
                        manual += 1
                        print(f'DUPE MANUELL: {file.stem} — {s.get("release", "")}: Altstatus ohne dauerhaftes Dateimanifest; keine Dateien verändert.')
                        continue
                    found += 1
                    cleanup(base, file.stem, apply=sys.argv[2] == '--apply', duplicate=True)
            except (OSError, ValueError, KeyError) as exc:
                errors += 1
                print(f'ERROR: {file.stem}: {exc}', file=sys.stderr)
        print(f'Dupe-Prüfung: {found} bestätigte prüfbare Aufträge; {manual} Altaufträge zur manuellen Prüfung; {errors} Fehler.')
        require(errors == 0, 'Dupe-Prüfung fehlgeschlagen')
    else:
        raise ValueError('Unbekannter Modus')

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
