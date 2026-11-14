"""Archive explicitly selected missing-transit states, retaining full backups.

Archived is an administrative outcome, not proof of upload or successful cleanup.
This module never unlinks media, posts uploads or modifies qBittorrent.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from audit_cleanup import torrents

CLOSED = {'archived', 'cleaned', 'skipped_duplicate', 'superseded'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def plain(path, directory=False):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), 'Umgeleiteter Pfad: ' + str(path))
    require(path.is_dir() if directory else path.is_file(), 'Datei/Ordner fehlt: ' + str(path))

@contextlib.contextmanager
def lock(path):
    import fcntl
    require(not path.is_symlink() and path.resolve() == path, 'Unsichere Sperrdatei')
    with open(path, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def no_transit(path, infos):
    require(isinstance(infos, list), 'Ungültige qBittorrent-Antwort')
    for info in infos:
        require(isinstance(info, dict), 'Ungültiger Torrent-Eintrag')
        for key in ('content_path', 'save_path'):
            value = info.get(key)
            require(isinstance(value, str) and Path(value).is_absolute(), 'Torrent-Pfad fehlt')
            p = Path(value)
            require(p != path and not p.is_relative_to(path) and not (key == 'content_path' and path.is_relative_to(p)), 'qBittorrent verwendet den Transitpfad')

def snapshot(file):
    plain(file)
    raw = file.read_bytes()
    state = json.loads(raw)
    require(isinstance(state, dict), 'Status ist kein Objekt')
    return raw, state

def reconcile(base, case, apply=False, fetch=torrents):
    job = case['job']
    require(re.fullmatch('[a-f0-9]{64}', job), 'Ungültige Job-ID')
    plain(base, True)
    require(base != base.parent, 'Unsichere Transitbasis')
    states = base / '_state'
    plain(states, True)
    path = Path(case['path'])
    require(path.parent == base and not path.name.startswith('.') and path.resolve() == path, 'Diagnosepfad passt nicht zur Transitbasis')
    file = states / (job + '.json')
    with lock(file.with_suffix('.lock')):
        original, state = snapshot(file)
        for key in ('path', 'release', 'submittedAt'):
            require(state.get(key) == case[key], 'Status passt nicht mehr zur Diagnose: ' + key)
        require(state.get('job') in (None, job), 'Job-ID widerspricht Dateiname')
        if state.get('status') in CLOSED:
            print(f'SKIP: {case["release"]}; Status={state["status"]}.')
            return False
        require(state.get('status') == 'accepted_unverified', 'Auftrag läuft oder besitzt einen anderen Status')
        names = {path.name, state.get('transitFolder', path.name)}
        require(all(isinstance(n, str) and n and not n.startswith('.') and '/' not in n and '\\' not in n for n in names), 'Ungültiger Transitname')
        with contextlib.ExitStack() as stack:
            for name in sorted(names):
                stack.enter_context(lock(states / (hashlib.sha256(name.encode()).hexdigest() + '.release.lock')))

            def validate():
                require(not os.path.lexists(path) and path.resolve() == path, 'Transit ist vorhanden oder umgeleitet; keine Archivierung')
                require(snapshot(file)[0] == original, 'Status wurde zwischenzeitlich verändert')
                for other in states.glob('*.json'):
                    if other != file and re.fullmatch('[a-f0-9]{64}', other.stem):
                        _, obj = snapshot(other)
                        require(obj.get('path') != str(path) or obj.get('status') in CLOSED, 'Ein anderer offener Auftrag verwendet denselben Transitpfad')

            validate()
            no_transit(path, fetch())
            print(f'ARCHIVE CHECK OK: {case["release"]}; Transit fehlt, Status kann mit Sicherung archiviert werden.')
            if not apply:
                print('Keine Statusdatei verändert. Keine Medien gelöscht.')
                return True
            # Repeat all live checks before changing the state; no target success is inferred.
            no_transit(path, fetch())
            validate()
            archive = states / '_archive'
            require(not archive.is_symlink() and archive.resolve() == archive, 'Archivordner wird umgeleitet')
            archive.mkdir(mode=0o700, exist_ok=True)
            plain(archive, True)
            backup = archive / (job + '.' + uuid.uuid4().hex + '.json')
            with open(backup, 'xb') as stream:
                stream.write(original)
                stream.flush(); os.fsync(stream.fileno())
            archived = dict(state)
            archived.update(status='archived', archiveReason='transit_missing_at_reconciliation',
                            archivePreviousStatus=state['status'], archivedAt=datetime.now(timezone.utc).isoformat(),
                            archiveBackup=str(backup), archiveOriginalSha256=hashlib.sha256(original).hexdigest(),
                            archiveNote='Administrative Archivierung ohne Löschung von Medien und ohne Bestätigung eines Upload-Erfolgs.')
            fd, temp = tempfile.mkstemp(prefix='.archive-state-', dir=states)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(archived, stream, ensure_ascii=False, indent=2)
                    stream.flush(); os.fsync(stream.fileno())
                validate()
                os.replace(temp, file)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            print(f'ARCHIVED: {case["release"]}; Sicherung={backup}; keine Medien gelöscht.')
            return True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', choices=['Radarr', 'Sonarr'])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name('reconcile-jobs.json').read_text(encoding='utf-8'))
    errors = 0
    for case in cases:
        if case['application'] != args.application:
            continue
        try:
            reconcile(Path(os.environ['TRANSIT_BASE']), case, args.apply)
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            print(f'ERROR: {case["release"]}: {detail}')
            errors += 1
    return 1 if errors else 0

if __name__ == '__main__':
    raise SystemExit(main())
