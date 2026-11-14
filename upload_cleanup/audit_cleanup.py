"""Read-only audit of locally stored ArrPorter jobs."""
import http.cookiejar
import json
import os
from pathlib import Path
import stat
import re
import urllib.parse
import urllib.request


def inspect(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return {'path': value if isinstance(value, str) else None, 'type': 'kein absoluter Pfad'}
    p = Path(value)
    result = {'path': str(p)}
    try:
        resolved = str(p.resolve())
        if resolved != str(p):
            result['resolved'] = resolved
        info = p.lstat()
        result['type'] = 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'special'
        if result['type'] == 'file':
            result['bytes'] = info.st_size
    except FileNotFoundError:
        result['type'] = 'missing'
    except OSError as exc:
        result['type'] = type(exc).__name__
    return result

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect')

def torrents():
    # Credentials and raw responses are deliberately never printed.
    url = os.environ['QBIT_URL'].rstrip('/')
    if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('scheme')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    data = urllib.parse.urlencode({'username': os.environ['QBIT_USER'], 'password': os.environ['QBIT_PASS']}).encode()
    with opener.open(url + '/api/v2/auth/login', data=data, timeout=30) as response:
        if response.read().strip() != b'Ok.':
            raise ValueError('login')
    with opener.open(url + '/api/v2/torrents/info', timeout=30) as response:
        result = json.load(response)
    if not isinstance(result, list):
        raise ValueError('response')
    return result

def main():
    app = os.environ['AUDIT_APP']
    base = Path(os.environ['TRANSIT_BASE'])
    print(json.dumps({'app': app, 'transitBase': inspect(str(base))}, ensure_ascii=False))
    try:
        entries = torrents()
        print('qBittorrent: Bestandsliste gelesen (keine Änderungen).')
    except Exception as exc:
        entries = None
        print('qBittorrent-Abfrage nicht möglich: ' + type(exc).__name__ + '; Dateiprüfung läuft weiter.')
    for job in sorted(p.stem for p in (base / '_state').glob('*.json') if re.fullmatch('[0-9a-f]{64}', p.stem)):
        file = base / '_state' / (job + '.json')
        try:
            if file.is_symlink() or file.resolve() != file:
                raise ValueError('redirected state')
            s = json.loads(file.read_text(encoding='utf-8'))
            release = s.get('release', '')
            transit = s.get('path', '')
            if (os.environ.get('SPECIAL_GROUP') and release.upper().endswith('-' + os.environ['SPECIAL_GROUP'].upper())):
                target = os.environ.get('CLIENT_SPECIAL_BASE') or '/data/torrents/special'
            else:
                target = os.environ.get('CLIENT_UPLOAD_BASE') or '/data/torrents/upload'
            names = {Path(value).name for value in (s.get('transitFolder'), release, transit) if isinstance(value, str) and value}
            names.discard(''); names.discard('.'); names.discard('..')
            observed = {}
            source = s.get('source')
            if isinstance(source, str):
                observed[source] = inspect(source)
            files = s.get('files', [])
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict):
                        for field in ('source', 'snapshot'):
                            value = item.get(field)
                            if isinstance(value, str):
                                observed[value] = inspect(value)
            matching = None if entries is None else [
                {key: t.get(key) for key in ('name', 'hash', 'category', 'auto_tmm', 'content_path', 'save_path', 'progress', 'amount_left', 'state')}
                for t in entries
                if isinstance(t, dict) and any(
                    str(t.get('name', '')).casefold() in (n.casefold(), Path(n).stem.casefold())
                    or Path(str(t.get('content_path') or '/')).name.casefold() == n.casefold()
                    or Path(str(t.get('content_path') or '/')).parent.name.casefold() == n.casefold()
                    for n in names
                )
            ]
            for t in matching or []:
                t['contentPathCheck'] = inspect(t.get('content_path'))
            print(json.dumps({
                'job': job, 'release': release, 'status': s.get('status'),
                'manifestVersion': s.get('manifestVersion'), 'storedJob': s.get('job'),
                'submittedAt': s.get('submittedAt'), 'cleanupUpdatedAt': s.get('cleanupUpdatedAt'),
                'transit': inspect(transit), 'targetBase': inspect(target),
                'expectedTargets': [inspect(str(Path(target) / name)) for name in sorted(names)],
                'sourceChecks': list(observed.values()),
                'matchingTorrents': matching,
            }, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({'job': job, 'stateCheck': inspect(str(file)), 'errorType': type(exc).__name__}))

if __name__ == '__main__':
    main()
