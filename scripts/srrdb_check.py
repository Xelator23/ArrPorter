"""Verify a transit inventory and retrieve original NFO bytes for CRC mismatches."""
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, build_opener
import zlib


VIDEOS = {'.mkv', '.mp4'}
SUBS = {'.idx', '.sub', '.srt', '.vtt', '.ass', '.ssa', '.sup'}
MEDIA = VIDEOS | SUBS | {'.nfo'}
LIMIT = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch(url):
    timeouts = (15, 30)
    for attempt, timeout in enumerate(timeouts, start=1):
        try:
            with build_opener(NoRedirect()).open(url, timeout=timeout) as response:
                data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('srrDB: Metadaten überschreiten das Größenlimit.')
            return data
        except (OSError, http.client.HTTPException) as error:
            cause = error.reason if isinstance(error, URLError) else error
            if isinstance(cause, TimeoutError) and attempt < len(timeouts):
                print(f'srrDB: Zeitüberschreitung beim Abruf von {url}. Neuer Versuch in 2 Sekunden.',
                      file=sys.stderr, flush=True)
                time.sleep(2)
                continue
            raise ValueError(f'srrDB: Abruf von {url} fehlgeschlagen (Versuch {attempt}/{len(timeouts)}): {error}.') from None


def relative(name):
    if not isinstance(name, str):
        raise ValueError('srrDB: Dateiname fehlt.')
    name = name.replace('\\', '/')
    if (any(x in {'', '.', '..'} for x in name.split('/'))
            or not name.isprintable() or ':' in name):
        raise ValueError('srrDB: Ungültiger Referenzpfad.')
    return PurePosixPath(name)


def record_crc(entries, path, crc):
    previous = entries.get(path)
    if previous is not None and crc is not None and previous != crc:
        raise ValueError(f'srrDB: Widersprüchliche CRC32 für {path}.')
    entries[path] = previous if crc is None else crc


def api_crc(row, path):
    value = row.get('crc')
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}', value):
        raise ValueError(f'srrDB: Ungültige CRC32 für {path}.')
    return int(value, 16)


def file_crc(path):
    checksum = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum = zlib.crc32(chunk, checksum)
    return checksum


def usable_nfo(data):
    text = data.decode('utf-8', errors='replace')
    media_dump = (re.search(r'^(General|Allgemein)\s*$', text, re.M | re.I)
                  and re.search(r'^[A-Za-z ]+ {10,}:', text, re.M)
                  and not re.search(r'imdb\.com|themoviedb\.org|director|handlung', text, re.I))
    return bool(text.strip()) and not media_dump


def store_nfo(path, data):
    """Publish verified bytes at a staging path without modifying existing hardlinks."""
    stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temp = Path(stream.name)
    try:
        with stream:
            stream.write(data)
        temp.chmod(0o644)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def subs_members(data):
    """Read SRR stored-file and RAR4 file headers. No extraction or decompression."""
    if not data.startswith(b'\x69\x69\x69'):
        raise ValueError('srrDB: Ungültige Subs-SRR.')
    offset = 0
    members = {}
    while offset < len(data):
        if offset + 7 > len(data):
            raise ValueError('srrDB: Abgeschnittener SRR-Block.')
        _, kind, flags, length = struct.unpack_from('<HBHH', data, offset)
        if length < 7 or offset + length > len(data):
            raise ValueError('srrDB: Ungültige SRR-Blocklänge.')
        header = data[offset:offset + length]
        offset += length
        if kind == 0x6A:  # Embedded metadata has a body, unlike archived RAR files.
            if length < 13:
                raise ValueError('srrDB: Ungültiger SRR-Dateiblock.')
            size, name_length = struct.unpack_from('<IH', header, 7)
            if name_length == 0 or 13 + name_length > length:
                raise ValueError('srrDB: Ungültiger gespeicherter Dateiname.')
            path = relative(header[13:13 + name_length].decode('utf-8'))
            end = offset + size
            if end > len(data):
                raise ValueError('srrDB: Unvollständige SRR-Datei.')
            if path.suffix.lower() in SUBS:
                record_crc(members, path, zlib.crc32(memoryview(data)[offset:end]))
            offset = end
        elif kind == 0x74:
            if length < 32 or flags & 0x0200:
                raise ValueError('srrDB: Unlesbarer oder nicht unterstützter RAR-Dateiname.')
            start = 40 if flags & 0x100 else 32
            end = start + int.from_bytes(header[26:28], 'little')
            if end <= start or end > length:
                raise ValueError('srrDB: Unvollständiger RAR-Dateiname.')
            path = relative(header[start:end].decode('utf-8'))
            if path.suffix.lower() in SUBS:
                # Only the last volume carries the CRC of the full unpacked file.
                crc = None if flags & 0x0002 else int.from_bytes(header[16:20], 'little')
                record_crc(members, path, crc)
        elif kind not in {0x69, 0x71, 0x72, 0x73, 0x7B}:
            raise ValueError(f'srrDB: Unbekannter Block in der Subs-SRR ({kind:#x}).')
    if not members:
        raise ValueError('srrDB: Subs-SRR ohne lesbare Untertitelliste.')
    return members


def reference(release, read):
    part = quote(release, safe='')
    try:
        details = json.loads(read('https://api.srrdb.com/v1/details/' + part))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('srrDB: Ungültige API-Antwort. Keine Übergabe an upPollo.') from error
    if details == []:
        return None
    if not isinstance(details, dict) or not isinstance(details.get('name'), str):
        raise ValueError('srrDB: Unerwartete Antwort auf die Release-Abfrage.')
    if details['name'] != release:
        raise ValueError(f'srrDB: Release-Name erwartet {details["name"]!r}, geplant {release!r}.')
    expected = {}
    sub_dirs = set()
    for key in ('files', 'archived-files'):
        rows = details.get(key)
        if not isinstance(rows, list):
            raise ValueError(f'srrDB: Dateiliste {key} fehlt.')
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('srrDB: Ungültiger Dateieintrag.')
            path = relative(row.get('name'))
            if any(x.lower() in {'sample', 'proof'} for x in path.parts[:-1]):
                continue
            suffix = path.suffix.lower()
            subtitle_area = (any(x.lower() in {'subs', 'subtitles', 'vobsubs'} for x in path.parts[:-1])
                             or re.search(r'[._-](?:vob)?subs?\.(?:srr|sfv|rar)$', path.name, re.I))
            if subtitle_area and suffix in {'.sfv', '.srr', '.rar'}:
                sub_dirs.add(path.parent)
            if suffix not in MEDIA and not (subtitle_area and suffix == '.srr'):
                continue
            crc = api_crc(row, path)
            if suffix == '.srr':
                data = read(f'https://www.srrdb.com/download/file/{part}/{quote(str(path))}')
                if zlib.crc32(data) != crc:
                    raise ValueError(f'srrDB: CRC32 der Metadaten fehlt oder stimmt nicht für {path}.')
                for member, member_crc in subs_members(data).items():
                    record_crc(expected, path.parent / member, member_crc)
            else:
                record_crc(expected, path, crc)
    for directory in sub_dirs:
        if not any(p.suffix.lower() in SUBS and directory in p.parents for p in expected):
            raise ValueError(f'srrDB: Untertitel unter {directory} angekündigt, Originalnamen nicht belegbar.')
    if not any(p.suffix.lower() in VIDEOS for p in expected):
        raise ValueError('srrDB: Keine prüfbare Videodatei im Release-Eintrag.')
    return expected


def check(release, folder, files, read=None, *, direct_candidate=False):
    """files maps final transit-relative names to local files; folder is None for direct MKVs."""
    read = read or fetch
    expected = reference(release, read)
    if expected is None:
        return f'srrDB: Kein Eintrag für {release}. Übergabe mit lokalen Prüfungen.', {}
    actual = {relative(name): Path(source) for name, source in files.items()}
    if direct_candidate and not any(path.suffix.lower() == '.nfo' for path in expected):
        folder = None
        actual = {PurePosixPath(path.name): source for path, source in actual.items()}
    problems = []
    requested_nfos = {}
    if folder is not None and folder != release:
        problems.append(f'Release-Ordner erwartet {release!r}, geplant {folder!r}')
    for path in sorted(expected.keys() - actual.keys()):
        if path.suffix.lower() != '.nfo':
            problems.append(f'fehlt: {path}')
        elif expected[path] is None:
            problems.append(f'CRC32-Referenz fehlt: {path}')
        elif any(p.startswith('.') for p in path.parts) or any(p in expected for p in path.parents) or any(path in p.parents for p in expected):
            problems.append(f'Unsicherer oder kollidierender NFO-Pfad: {path}')
        else:
            requested_nfos[path] = None
    for path in sorted(actual.keys() - expected.keys()):
        problems.append(f'unerwarteter Name/Pfad: {path}')
    for path in sorted(expected.keys() & actual.keys()):
        if expected[path] is None:
            problems.append(f'CRC32-Referenz fehlt: {path}')
            continue
        checksum = file_crc(actual[path])
        if checksum != expected[path]:
            if path.suffix.lower() == '.nfo':
                requested_nfos[path] = checksum
            else:
                problems.append(f'CRC32 weicht ab: {path} (srrDB {expected[path]:08X}, lokal {checksum:08X})')
    if problems:
        raise ValueError('srrDB: ' + '; '.join(problems) + '. Keine Übergabe an upPollo.')
    replacements = {}
    messages = []
    for path, local_crc in requested_nfos.items():
        url = f'https://www.srrdb.com/download/file/{quote(release, safe="")}/{quote(path.as_posix())}'
        try:
            data = read(url)
        except (OSError, ValueError) as error:
            raise ValueError(f'srrDB: Original-NFO {path} nicht abrufbar: {error}. Keine Übergabe an upPollo.') from error
        crc = zlib.crc32(data)
        if crc != expected[path]:
            raise ValueError(f'srrDB: CRC32 der geladenen NFO weicht ab: {path} '
                             f'(srrDB {expected[path]:08X}, Download {crc:08X}). Keine Übergabe an upPollo.')
        replacements[path.as_posix()] = data
        local = 'lokal fehlend' if local_crc is None else f'lokal {local_crc:08X}'
        messages.append(f'srrDB: Original-NFO geladen und geprüft: {path} ({local}, Original {crc:08X}).')
    messages.append(f'srrDB OK: {release}, {len(expected)} Dateinamen, Pfade und CRC32-Prüfsummen stimmen überein.')
    return '\n'.join(messages), replacements
