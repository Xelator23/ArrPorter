"""Self-contained Sonarr cleanup primitives; no import/uploader dependency."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
import urllib.request

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def require(test, message):
    if not test:
        raise ValueError(message)


def safe_part(part):
    require(bool(part) and not part.startswith('.') and '/' not in part and '\\' not in part
            and not any(ord(c) < 32 or ord(c) == 127 for c in part), 'Unsicherer Originalname')
    return part


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



def error_detail(error):
    if isinstance(error, FileNotFoundError) and error.filename:
        return 'Datei oder Ordner fehlt: ' + str(error.filename)
    return str(error) if isinstance(error, ValueError) else type(error).__name__
