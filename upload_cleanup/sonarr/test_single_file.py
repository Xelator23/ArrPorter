from argparse import Namespace
import contextlib
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cleanup_support as s
import cleanup_duplicates as d
import cleanup_single_file as c
from test_duplicates import dupe_lines, BEFORE, state_for_path




class FileCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.base = self.root / 'transit'; self.base.mkdir()
        (self.base / '_state').mkdir()
        self.job = 'd' * 64
        self.folder = 'Release.S01E01-G'
        self.name = 'original.mkv'
        self.spool = self.base / '_events' / self.job
        self.spool.mkdir(parents=True)
        self.library = self.root / 'library'; self.library.mkdir()
        self.video = self.library / 'Renamed.mkv'; self.video.write_bytes(b'original bytes')
        self.dest = self.base / self.name
        os.link(self.video, self.dest)
        self.neighbor = self.base / 'other-episode.mkv'; self.neighbor.write_bytes(b'neighbor')
        self.upload = self.root / 'upload'; self.upload.mkdir()
        self.target = self.upload / self.name
        os.link(self.video, self.target)
        self.state_path = self.base / '_state' / (self.job + '.json')
        state = dict(state_for_path(str(self.dest)), application='sonarr', release=self.folder,
                     transitFolder=self.folder, transitKind='file', transitName=self.name,
                     downloadId='download', source='/download/Release')
        s.write_json(self.state_path, state)
        plan = {'root': state['source'], 'transitFolder': self.folder, 'transitKind': 'file',
                'transitName': self.name, 'release': self.folder,
                'videos': [{'relative': self.name, 'imported': str(self.video)}], 'extras': []}
        s.write_json(self.spool / 'plan.json', plan)
        s.write_json(self.spool / 'latest-event.json', {
            'sonarr_download_id': 'download', 'sonarr_series_path': str(self.library),
            'sonarr_episodefile_paths': str(self.video)})
        self.config = {'TRANSIT_BASE': str(self.base), 'CLIENT_UPLOAD_BASE': str(self.upload)}
        self.info = dict(content_path=str(self.target), save_path=str(self.upload), hash='e' * 40,
                         progress=1, amount_left=0, state='stalledUP')
        self.files = [dict(name=self.name, size=self.video.stat().st_size, progress=1)]
        for p in (patch.object(s, 'lock', lambda _: contextlib.nullcontext()),
                  patch.object(d, 'qbit_info', side_effect=lambda *_: [self.info.copy()]),
                  patch.object(d, 'qbit_files', side_effect=lambda *_: copy.deepcopy(self.files))):
            p.start()
            self.addCleanup(p.stop)

    def test_success_check_and_cleanup_only_unlink_the_single_mkv(self):
        original_state = self.state_path.read_bytes()
        c.cleanup(self.config, self.job)
        self.assertEqual(self.state_path.read_bytes(), original_state)
        self.assertTrue(self.dest.exists())
        c.cleanup(self.config, self.job, True)
        self.assertFalse(self.dest.exists())
        self.assertTrue(self.base.is_dir())
        self.assertEqual(self.video.read_bytes(), b'original bytes')
        self.assertEqual(self.target.read_bytes(), b'original bytes')
        self.assertEqual(self.neighbor.read_bytes(), b'neighbor')
        self.assertEqual(d.read_json(self.state_path)['status'], 'cleaned')
        self.dest.write_bytes(b'later job')
        c.cleanup(self.config, self.job, True)
        self.assertEqual(self.dest.read_bytes(), b'later job')

    def test_upstream_staging_in_named_directory_is_verified_by_exact_torrent_path(self):
        self.target.unlink()
        directory = self.upload / self.folder; directory.mkdir()
        self.target = directory / self.name
        os.link(self.video, self.target)
        self.info['content_path'] = str(directory)
        self.files[0]['name'] = self.folder + '/' + self.name
        c.cleanup(self.config, self.job, True)
        self.assertTrue(self.target.exists())
        self.assertFalse(self.dest.exists())

    def test_incomplete_ambiguous_or_transit_seeding_torrent_blocks(self):
        original = self.info.copy()
        for values in ({'progress': 0.5}, {'state': 'checkingUP'}, {'content_path': str(self.dest)}):
            self.info.update(values)
            with self.assertRaises(ValueError):
                c.cleanup(self.config, self.job, True)
            self.info = original.copy()
        with patch.object(d, 'qbit_info', return_value=[self.info, self.info.copy()]):
            with self.assertRaisesRegex(ValueError, 'eindeutige'):
                c.cleanup(self.config, self.job, True)
        self.assertTrue(self.dest.exists())

    def test_wrong_torrent_file_path_or_replaced_target_blocks(self):
        self.files[0]['name'] = 'different.mkv'
        with self.assertRaises(ValueError):
            c.cleanup(self.config, self.job, True)
        self.files[0]['name'] = self.name
        self.target.unlink()
        self.target.write_bytes(b'different data')
        with self.assertRaises(ValueError):
            c.cleanup(self.config, self.job, True)
        self.assertTrue(self.dest.exists())

    def test_dupe_cleanup_only_unlinks_file_and_preserves_transit_base(self):
        attempts = d.parse_attempts(dupe_lines())
        attempts[0]['path'] = str(self.dest)
        d.cleanup_job(self.config, self.job, attempts, BEFORE, False)
        self.assertTrue(self.dest.exists())
        d.cleanup_job(self.config, self.job, attempts, BEFORE, True)
        self.assertFalse(self.dest.exists())
        self.assertTrue(self.base.is_dir())
        self.assertTrue(self.spool.is_dir())
        self.assertTrue(self.video.exists())
        self.assertTrue(self.neighbor.exists())
        self.assertEqual(d.read_json(self.state_path)['status'], 'skipped_duplicate')

    def test_file_state_cannot_delete_directory_or_traverse(self):
        self.dest.unlink(); self.dest.mkdir()
        (self.dest / 'keep').write_bytes(b'keep')
        with self.assertRaises(ValueError):
            c.cleanup(self.config, self.job, True)
        state = d.read_json(self.state_path)
        state['transitName'] = '../library/Renamed.mkv'
        s.write_json(self.state_path, state)
        with self.assertRaises(ValueError):
            c.cleanup(self.config, self.job, True)
        self.assertEqual((self.dest / 'keep').read_bytes(), b'keep')

    def test_retry_after_unlink_before_status_update_checks_target_and_finishes(self):
        state = d.read_json(self.state_path)
        state['status'] = 'cleanup_started'
        s.write_json(self.state_path, state)
        self.dest.unlink()
        c.cleanup(self.config, self.job, True)
        self.assertEqual(d.read_json(self.state_path)['status'], 'cleaned')
        self.assertTrue(self.target.exists())
        self.assertTrue(self.neighbor.exists())


if __name__ == '__main__':
    unittest.main()
