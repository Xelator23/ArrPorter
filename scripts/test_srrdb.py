import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from srrdb_check import check, subs_members


def block(kind, payload=b'', flags=0):
    return struct.pack('<HBHH', kind * 257, kind, flags, 7 + len(payload)) + payload


def archive_member(name):
    raw = name.encode()
    header = struct.pack('<IIBIIBBHI', 3, 3, 2, 0, 0, 20, 48, len(raw), 32)
    return block(0x74, header + raw, 0x8000)


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
            {'name': 'movie.nfo', 'size': 3}, {'name': 'Sample/test.mkv', 'size': 1},
            {'name': 'Subs/movie.srr', 'size': len(self.srr), 'crc': f'{zlib.crc32(self.srr):08X}'}],
            'archived-files': [{'name': 'movie.mkv', 'size': 3}]}

    def read(self, url):
        if url == 'https://api.srrdb.com/v1/details/Movie-G':
            return json.dumps(self.details).encode()
        self.assertEqual(url, 'https://www.srrdb.com/download/file/Movie-G/Subs/movie.srr')
        return self.srr

    def run_check(self, folder='Movie-G'):
        return check('Movie-G', folder, self.files, self.read)

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

    def test_wrong_size_and_invalid_metadata(self):
        self.details['archived-files'][0]['size'] = 2
        with self.assertRaisesRegex(ValueError, 'Größe weicht ab'):
            self.run_check()
        self.details['files'][2]['crc'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            self.run_check()

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
