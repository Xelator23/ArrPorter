"""Read-only comparison of a proposed transit inventory with srrDB metadata."""
import http.client
import json
from pathlib import Path, PurePosixPath
import re
import struct
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
    try:
        with build_opener(NoRedirect()).open(url, timeout=15) as response:
            data = response.read(LIMIT + 1)
    except (OSError, URLError, http.client.HTTPException) as error:
        raise ValueError(f'srrDB: Metadaten nicht abrufbar ({error}).') from None
    if len(data) > LIMIT:
        raise ValueError('srrDB: Metadaten überschreiten das Größenlimit.')
    return data


def relative(name):
    if not isinstance(name, str):
        raise ValueError('srrDB: Dateiname fehlt.')
    name = name.replace('\\', '/')
    if (any(x in {'', '.', '..'} for x in name.split('/'))
            or not name.isprintable() or ':' in name):
        raise ValueError('srrDB: Ungültiger Referenzpfad.')
    return PurePosixPath(name)


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
            if path.suffix.lower() in SUBS:
                members[path] = size
            offset += size
            if offset > len(data):
                raise ValueError('srrDB: Unvollständige SRR-Datei.')
        elif kind == 0x74:
            if length < 32 or flags & 0x0200:
                raise ValueError('srrDB: Unlesbarer oder nicht unterstützter RAR-Dateiname.')
            size = int.from_bytes(header[11:15], 'little')
            start = 40 if flags & 0x100 else 32
            if start == 40:
                size += int.from_bytes(header[36:40], 'little') << 32
            end = start + int.from_bytes(header[26:28], 'little')
            if end <= start or end > length:
                raise ValueError('srrDB: Unvollständiger RAR-Dateiname.')
            path = relative(header[start:end].decode('utf-8'))
            if path.suffix.lower() in SUBS:
                if path in members and members[path] != size:
                    raise ValueError(f'srrDB: Widersprüchliche Angaben zu {path}.')
                members[path] = size
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
            size = row.get('size')
            if type(size) is not int or size < 0:
                raise ValueError(f'srrDB: Größenangabe fehlt für {path}.')
            if suffix == '.srr':
                if size > LIMIT:
                    raise ValueError(f'srrDB: Subs-Metadaten zu groß: {path}.')
                data = read(f'https://www.srrdb.com/download/file/{part}/{quote(str(path))}')
                if len(data) != size or f'{zlib.crc32(data):08X}' != str(row.get('crc')).upper():
                    raise ValueError(f'srrDB: Größe oder CRC32 der Metadaten stimmt nicht für {path}.')
                entries = {path.parent / p: n for p, n in subs_members(data).items()}
            else:
                entries = {path: size}
            for entry, length in entries.items():
                if entry in expected and expected[entry] != length:
                    raise ValueError(f'srrDB: Widersprüchliche Größe für {entry}.')
                expected[entry] = length
    for directory in sub_dirs:
        if not any(p.suffix.lower() in SUBS and directory in p.parents for p in expected):
            raise ValueError(f'srrDB: Untertitel unter {directory} angekündigt, Originalnamen nicht belegbar.')
    if not any(p.suffix.lower() in VIDEOS for p in expected):
        raise ValueError('srrDB: Keine prüfbare Videodatei im Release-Eintrag.')
    return expected


def check(release, folder, files, read=None):
    """files maps final transit-relative names to local files; folder is None for direct MKVs."""
    expected = reference(release, read or fetch)
    if expected is None:
        return f'srrDB: Kein Eintrag für {release}. Übergabe mit lokalen Prüfungen.'
    actual = {relative(name): Path(source) for name, source in files.items()}
    problems = []
    if folder is not None and folder != release:
        problems.append(f'Release-Ordner erwartet {release!r}, geplant {folder!r}')
    for path in sorted(expected.keys() - actual.keys()):
        problems.append(f'fehlt: {path}')
    for path in sorted(actual.keys() - expected.keys()):
        problems.append(f'unerwarteter Name/Pfad: {path}')
    for path in sorted(expected.keys() & actual.keys()):
        if actual[path].stat().st_size != expected[path]:
            problems.append(f'Größe weicht ab: {path}')
    if problems:
        raise ValueError('srrDB: ' + '; '.join(problems) + '. Keine Übergabe an upPollo.')
    return f'srrDB OK: {release}, {len(expected)} Dateinamen, Pfade und Größen stimmen überein.'
