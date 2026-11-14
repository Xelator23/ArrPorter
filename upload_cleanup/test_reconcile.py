import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import reconcile as r


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve() / 'transit'
        self.states = self.base / '_state'
        self.states.mkdir(parents=True)
        self.job = 'a' * 64
        self.case = dict(job=self.job, path=str(self.base / 'Release'), release='Release', submittedAt='2026-09-16T12:00:00Z')
        self.file = self.states / (self.job + '.json')
        self.original = dict(self.case, status='accepted_unverified', privateMetadata={'keep': [1, 2]})
        self.file.write_text(json.dumps(self.original), encoding='utf-8')
        self.bytes = self.file.read_bytes()
        self.target = self.base.parent / 'library.mkv'
        self.target.write_bytes(b'library media')
        if os.name == 'nt':
            p = patch.object(r, 'lock', lambda _: contextlib.nullcontext())
            p.start(); self.addCleanup(p.stop)

    def run_case(self, apply=True, fetch=lambda: []):
        return r.reconcile(self.base, self.case, apply, fetch)

    def test_check_does_not_modify_state_or_media(self):
        self.run_case(False)
        self.assertEqual(self.file.read_bytes(), self.bytes)
        self.assertFalse((self.states / '_archive').exists())
        self.assertEqual(self.target.read_bytes(), b'library media')

    def test_apply_backs_up_original_and_archives_without_success_claim(self):
        self.run_case()
        s = json.loads(self.file.read_text())
        self.assertEqual(s['status'], 'archived')
        self.assertEqual(s['archivePreviousStatus'], 'accepted_unverified')
        self.assertEqual(Path(s['archiveBackup']).read_bytes(), self.bytes)
        self.assertEqual(s['privateMetadata'], self.original['privateMetadata'])
        self.assertEqual(self.target.read_bytes(), b'library media')
        self.assertNotIn('cleanupUpdatedAt', s)
        self.assertNotIn('targetTorrentHash', s)
        self.assertEqual(self.run_case(), False)
        self.assertEqual(len(list((self.states / '_archive').iterdir())), 1)

    def test_present_transit_keeps_state(self):
        Path(self.case['path']).mkdir()
        with self.assertRaisesRegex(ValueError, 'vorhanden'):
            self.run_case()
        self.assertEqual(self.file.read_bytes(), self.bytes)

    def test_qbit_reference_keeps_state(self):
        with self.assertRaisesRegex(ValueError, 'qBittorrent'):
            self.run_case(fetch=lambda: [{'content_path': self.case['path'], 'save_path': str(self.base)}])
        self.assertEqual(self.file.read_bytes(), self.bytes)

    def test_path_reappears_during_query(self):
        calls = []
        def fetch():
            calls.append(1)
            if len(calls) == 2:
                Path(self.case['path']).write_bytes(b'new job')
            return []
        with self.assertRaisesRegex(ValueError, 'vorhanden'):
            self.run_case(fetch=fetch)
        self.assertEqual(self.file.read_bytes(), self.bytes)
        self.assertEqual(Path(self.case['path']).read_bytes(), b'new job')

    def test_changed_submission_rejected(self):
        self.case['submittedAt'] = '2026-09-17T12:00:00Z'
        with self.assertRaisesRegex(ValueError, 'Diagnose'):
            self.run_case()

    def test_other_open_job_blocks(self):
        (self.states / ('b' * 64 + '.json')).write_text(json.dumps(self.original))
        with self.assertRaisesRegex(ValueError, 'anderer offener'):
            self.run_case()

    def test_active_state_blocks(self):
        self.original['status'] = 'submitting'
        self.file.write_text(json.dumps(self.original))
        with self.assertRaisesRegex(ValueError, 'anderen Status'):
            self.run_case()

    def test_config_for_other_base_rejected(self):
        self.case['path'] = str(self.base.parent / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'Transitbasis'):
            self.run_case()

    def test_invalid_qbit_response_rejected(self):
        with self.assertRaisesRegex(ValueError, 'qBittorrent'):
            self.run_case(fetch=lambda: {})


if __name__ == '__main__':
    unittest.main()
