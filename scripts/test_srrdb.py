import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from srrdb_check import check, subs_members


def block(kind, payload=b'', flags=0):
    return struct.pack('<HBHH', kind * 257, kind, flags, 7 + len(payload)) + payload


def archive_member(name, crc=0x352441C2, flags=0x8000):
    raw = name.encode()
    header = struct.pack('<IIBIIBBHI', 3, 3, 2, crc, 0, 20, 48, len(raw), 32)
    return block(0x74, header + raw, flags)


class CheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / 'library-name.mkv'
        source.write_bytes(b'abc')
        names = ('Subs/movie.idx', 'Subs/movie.sub', 'movie.nfo', 'movie.mkv')
        self.files = dict.fromkeys(names, source)
        stored = block(0x6a, struct.pack('<IH', 3, 4) + b'note', 0x8000) + b'abc'
        self.srr = block(0x69) + stored + archive_member('movie.idx') + archive_member('movie.sub')
        self.details = {'name': 'Movie-G', 'files': [
            {'name': 'movie.nfo', 'size': 3, 'crc': '352441c2'}, {'name': 'Sample/test.mkv', 'size': 1},
            {'name': 'Subs/movie.srr', 'size': len(self.srr), 'crc': f'{zlib.crc32(self.srr):08X}'}],
            'archived-files': [{'name': 'movie.mkv', 'size': 3, 'crc': '352441C2'}]}

    def read(self, url):
        if url == 'https://api.srrdb.com/v1/details/Movie-G':
            return json.dumps(self.details).encode()
        if url == 'https://www.srrdb.com/download/file/Movie-G/movie.nfo':
            return b'abc'
        self.assertEqual(url, 'https://www.srrdb.com/download/file/Movie-G/Subs/movie.srr')
        return self.srr

    def run_check(self, folder='Movie-G'):
        return check('Movie-G', folder, self.files, self.read)[0]

    def test_all_present(self):
        self.assertIn('4 Dateinamen', self.run_check())

    def test_missing_and_wrong_case(self):
        self.files['Subs/MOVIE.idx'] = self.files.pop('Subs/movie.idx')
        del self.files['Subs/movie.sub']
        with self.assertRaises(ValueError) as caught:
            self.run_check()
        for message in ['fehlt: Subs/movie.idx', 'fehlt: Subs/movie.sub', 'unerwarteter Name/Pfad: Subs/MOVIE.idx']:
            self.assertIn(message, str(caught.exception))

    def test_directory_and_single_file(self):
        with self.assertRaisesRegex(ValueError, 'Release-Ordner erwartet'):
            self.run_check('movie-g')
        self.details['files'] = []
        self.files = {'movie.mkv': self.files['movie.mkv']}
        self.assertIn('1 Dateinamen', self.run_check(None))

    def test_crc_independent_of_reported_size(self):
        self.details['archived-files'][0]['size'] = 2
        self.assertIn('CRC32', self.run_check())
        self.details['files'][0]['crc'] = '00000000'
        with self.assertRaisesRegex(ValueError, r'movie\.nfo \(srrDB 00000000, Download 352441C2\)'):
            self.run_check()

    def test_changed_video_and_subtitles(self):
        source = self.files['movie.mkv']
        source.write_bytes(b'abd')
        with self.assertRaises(ValueError) as caught:
            self.run_check()
        for name in ('Subs/movie.idx', 'Subs/movie.sub', 'movie.mkv'):
            self.assertIn('CRC32 weicht ab: ' + name, str(caught.exception))

    def test_recover_only_wrong_nfo_with_exact_original_bytes(self):
        source = self.files['movie.nfo'].with_name('raw.nfo')
        source.write_bytes(b'abc\r\n')
        self.files['movie.nfo'] = source
        report, originals = check('Movie-G', 'Movie-G', self.files, self.read)
        self.assertEqual(originals, {'movie.nfo': b'abc'})
        self.assertIn('Original-NFO geladen und geprüft: movie.nfo', report)
        self.assertEqual(source.read_bytes(), b'abc\r\n')
        def unavailable(url):
            if url.endswith('movie.nfo'):
                raise OSError('404')
            return self.read(url)
        with self.assertRaisesRegex(ValueError, 'movie.nfo nicht abrufbar'):
            check('Movie-G', 'Movie-G', self.files, unavailable)

    def test_streamed_video(self):
        data = bytes(range(256)) * 5000
        self.files['movie.mkv'].write_bytes(data)
        self.files = {'movie.mkv': self.files['movie.mkv']}
        self.details['files'] = []
        self.details['archived-files'][0]['crc'] = f'{zlib.crc32(data):08x}'
        self.assertIn('CRC32', self.run_check())

    def test_missing_invalid_and_conflicting_reference(self):
        del self.details['files'][0]['crc']
        with self.assertRaisesRegex(ValueError, 'CRC32-Referenz fehlt: movie.nfo'):
            self.run_check()
        self.details['files'].append({'name': 'movie.mkv', 'crc': '00000000'})
        with self.assertRaisesRegex(ValueError, 'Widersprüchliche CRC32'):
            self.run_check()
        self.details['files'].pop()
        self.details['files'][2]['crc'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            self.run_check()

    def test_stored_and_split_subtitle_crc(self):
        name = b'movie.idx'
        stored = block(0x6a, struct.pack('<IH', 3, len(name)) + name, 0x8000) + b'abc'
        first = archive_member('movie.sub', 0x12345678, 0x8002)
        final = archive_member('movie.sub', 0x352441C2, 0x8001)
        members = subs_members(block(0x69) + stored + first + final)
        self.assertEqual({str(p): crc for p, crc in members.items()},
                         {'movie.idx': 0x352441C2, 'movie.sub': 0x352441C2})
        self.assertEqual(list(subs_members(block(0x69) + first).values()), [None])

    def test_unknown_release_and_errors(self):
        self.details = []
        self.assertIn('Kein Eintrag', self.run_check())
        self.details = {'error': 'service unavailable'}
        with self.assertRaises(ValueError):
            self.run_check()
        with self.assertRaises(ValueError):
            check('Movie-G', None, {}, lambda url: b'<html>')

    def test_unresolved_subs_and_malformed_headers(self):
        self.details['files'] = [{'name': 'Subs/movie.sfv', 'size': 31}]
        with self.assertRaisesRegex(ValueError, 'nicht belegbar'):
            self.run_check()
        for invalid in [b'', self.srr[:-1], block(0x69) + archive_member('../sample.idx')]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                subs_members(invalid)
