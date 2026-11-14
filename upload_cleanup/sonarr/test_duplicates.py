import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cleanup_duplicates as d


START = '2026-09-11T10:50:48.100+02:00'
FINISH = '2026-09-11T10:50:51.010+02:00'
PATH = '/data/.uppollo_transit/sonarr/Example.S01E12.1080p-GROUP'
BEFORE = d.seconds('2026-09-11T11:00:00+02:00')


def line(message, when=FINISH):
    return f'time={when} level=INFO msg={json.dumps(message, ensure_ascii=False)}\n'


def dupe_lines():
    return [line('New Upload: ' + PATH, START),
            line('TMDb: Failed to fetch data for season 0'),
            line('found dupe: Example (exact size match 123=123)'),
            line(d.DUPE_SKIP), line('Cleaning Up!'), line(d.TERMINAL_ERROR)]


def state_for_path(path=PATH):
    return {'path': path, 'submittedAt': '2026-09-11T08:50:48Z',
            'updatedAt': '2026-09-11T08:50:48Z', 'status': 'accepted_unverified'}


class LogTests(unittest.TestCase):
    def test_explicit_dupe_and_terminal_failure_are_required(self):
        attempts = d.parse_attempts(dupe_lines())
        self.assertEqual(d.match_attempt(state_for_path(), attempts, BEFORE),
                         {'path': PATH, 'start': START, 'finish': FINISH})
        for sequence in (
            [line('New Upload: ' + PATH, START), line(d.TERMINAL_ERROR)],
            [line('New Upload: ' + PATH, START), line('found dupe: x'), line(d.TERMINAL_ERROR)],
            [line('New Upload: ' + PATH, START), line(d.DUPE_SKIP), line(d.TERMINAL_ERROR)],
            dupe_lines()[:-1], dupe_lines()[1:],
            dupe_lines()[:-1] + [line('Error during upload processing: timeout')],
            dupe_lines()[:-1] + [line(d.TERMINAL_SUCCESS)],
        ):
            with self.subTest(sequence=sequence):
                self.assertIsNone(d.match_attempt(state_for_path(), d.parse_attempts(sequence), BEFORE))

    def test_other_tracker_skip_is_not_treated_as_dupe_only(self):
        for message in ('OtherTracker: skipped · connection failed', 'Other Tracker: pending',
                        'Other Tracker: uploaded', 'RocketHD: skipped · filter failed'):
            logs = dupe_lines()[:-1] + [line(message), line(d.TERMINAL_ERROR)]
            self.assertIsNone(d.match_attempt(state_for_path(), d.parse_attempts(logs), BEFORE))

    def test_overlapping_jobs_are_rejected_and_later_job_can_recover(self):
        overlap = [line('New Upload: /other', START)] + dupe_lines() + [line(d.TERMINAL_ERROR)]
        self.assertIsNone(d.match_attempt(state_for_path(), d.parse_attempts(overlap), BEFORE))
        later = [entry.replace(PATH, '/another').replace('10:50:', '10:51:') for entry in dupe_lines()]
        attempts = d.parse_attempts(overlap + later)
        state = state_for_path('/another')
        state.update(submittedAt='2026-09-11T08:51:48Z', updatedAt='2026-09-11T08:51:48Z')
        self.assertIsNotNone(d.match_attempt(state, attempts, BEFORE))

    def test_later_start_for_same_path_blocks_even_without_a_finish(self):
        logs = dupe_lines() + [line('New Upload: ' + PATH, '2026-09-11T10:59:00+02:00')]
        self.assertIsNone(d.match_attempt(state_for_path(), d.parse_attempts(logs), BEFORE))

    def test_old_future_or_unordered_log_cannot_confirm_this_submission(self):
        attempts = d.parse_attempts(dupe_lines())
        state = dict(state_for_path(), submittedAt='2026-09-11T08:51:00Z')
        self.assertIsNone(d.match_attempt(state, attempts, BEFORE))
        self.assertIsNone(d.match_attempt(state_for_path(), attempts, d.seconds(FINISH)))
        unordered = dupe_lines()[:-1] + [line('Preparing', '2026-09-11T10:40:00+02:00'), line(d.TERMINAL_ERROR)]
        self.assertIsNone(d.match_attempt(state_for_path(), d.parse_attempts(unordered), BEFORE))

    def test_midnight_log_rotation_and_previous_unrelated_success(self):
        logs = [line(d.TERMINAL_SUCCESS)] + dupe_lines()
        logs = [entry.replace(START, '2026-09-10T23:59:58+02:00').replace(FINISH, '2026-09-11T00:00:02+02:00') for entry in logs]
        state = dict(state_for_path(), submittedAt='2026-09-10T21:59:58Z', updatedAt='2026-09-10T21:59:58Z')
        self.assertIsNotNone(d.match_attempt(state, d.parse_attempts(logs), BEFORE))

    def test_torrent_referencing_transit_blocks_cleanup(self):
        dest = Path('/data/transit/release')
        for info in (
            [{'content_path': '/data/transit/release/a.mkv', 'save_path': '/data/transit'}],
            [{'content_path': '/other', 'save_path': '/data/transit/release'}],
            [{'content_path': '/data/transit', 'save_path': '/data'}],
            [{'content_path': '', 'save_path': ''}],
        ):
            with self.assertRaises(ValueError):
                d.no_torrent_uses(dest, info)
        d.no_torrent_uses(dest, [{'content_path': '/data/transit/release-other', 'save_path': '/data/transit'}])


class CleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.base = self.root / 'transit'
        self.base.mkdir()
        self.job = 'a' * 64
        self.folder = 'Example.S01E12.1080p-GROUP'
        self.dest = self.base / self.folder
        self.dest.mkdir()
        (self.base / '_state').mkdir()
        self.spool = self.base / '_events' / self.job
        self.snapshots = self.spool / 'extras'
        self.snapshots.mkdir(parents=True)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.video = self.library / 'Renamed Episode.mkv'
        self.video.write_bytes(b'video-content')
        self.snapshot = self.snapshots / 'nfo.extra'
        self.snapshot.write_bytes(b'original nfo')
        os.link(self.video, self.dest / 'original.mkv')
        os.link(self.snapshot, self.dest / 'original.nfo')
        self.state_path = self.base / '_state' / (self.job + '.json')
        state = dict(state_for_path(str(self.dest)), application='sonarr', release=self.folder,
                     transitFolder=self.folder, downloadId='download', source='/original/source')
        d.s.write_json(self.state_path, state)
        d.s.write_json(self.spool / 'plan.json', {
            'transitFolder': self.folder, 'release': self.folder, 'root': '/original/source',
            'videos': [{'relative': 'original.mkv', 'imported': str(self.video)}],
            'extras': [{'relative': 'original.nfo', 'snapshot': str(self.snapshot), 'sha256': d.digest(self.snapshot)}]})
        d.s.write_json(self.spool / 'latest-event.json', {
            'sonarr_download_id': 'download', 'sonarr_series_path': str(self.library),
            'sonarr_episodefile_paths': str(self.video)})
        self.attempts = d.parse_attempts(dupe_lines())
        self.attempts[0]['path'] = str(self.dest)
        self.config = {'TRANSIT_BASE': str(self.base)}
        self.other = self.base / '_state' / ('b' * 64 + '.json')
        d.s.write_json(self.other, {'path': 'other-release', 'status': 'accepted_unverified'})
        self.other_before = self.other.read_bytes()
        for mock in [patch.object(d.s, 'lock', lambda _: contextlib.nullcontext()),
                     patch.object(d, 'qbit_info', return_value=[])]:
            mock.start()
            self.addCleanup(mock.stop)

    def cleanup(self, apply=True):
        return d.cleanup_job(self.config, self.job, self.attempts, BEFORE, apply)

    def test_check_and_apply_preserve_library_snapshots_other_jobs_and_prevent_replay(self):
        initial = self.state_path.read_bytes()
        self.cleanup(False)
        self.assertEqual(self.state_path.read_bytes(), initial)
        self.assertEqual(len(list(self.dest.iterdir())), 2)
        self.cleanup()
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.video.read_bytes(), b'video-content')
        self.assertEqual(self.snapshot.read_bytes(), b'original nfo')
        self.assertEqual(self.other.read_bytes(), self.other_before)
        state = d.read_json(self.state_path)
        self.assertEqual(state['status'], 'skipped_duplicate')
        self.assertEqual(state['duplicateEvidence']['start'], START)
        self.dest.mkdir()
        (self.dest / 'new-import.mkv').write_bytes(b'new')
        self.assertFalse(self.cleanup())
        self.assertTrue((self.dest / 'new-import.mkv').exists())

    def test_missing_or_replaced_library_file_keeps_entire_transit(self):
        self.video.unlink()
        with self.assertRaises(OSError):
            self.cleanup()
        self.video.write_bytes(b'other-content')
        with self.assertRaisesRegex(ValueError, 'stimmt nicht'):
            self.cleanup()
        self.assertEqual(len(list(self.dest.iterdir())), 2)
        self.assertEqual(d.read_json(self.state_path)['status'], 'accepted_unverified')

    def test_corrupt_snapshot_is_rejected_even_when_transit_shares_same_inode(self):
        self.snapshot.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Hash'):
            self.cleanup()
        self.assertTrue(self.dest.is_dir())

    def test_unplanned_file_or_missing_planned_file_preserves_all_remaining_data(self):
        extra = self.dest / 'unexpected.nfo'
        extra.write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'ungeplante'):
            self.cleanup()
        extra.unlink()
        (self.dest / 'original.nfo').unlink()
        with self.assertRaisesRegex(ValueError, 'fehlende'):
            self.cleanup()
        self.assertTrue((self.dest / 'original.mkv').exists())

    def test_another_open_job_on_same_folder_blocks(self):
        d.s.write_json(self.other, {'path': str(self.dest), 'status': 'accepted_unverified'})
        with self.assertRaisesRegex(ValueError, 'weiterer offener Auftrag'):
            self.cleanup()
        self.assertTrue(self.dest.is_dir())

    def test_qbit_error_and_failed_second_check_do_not_delete(self):
        with patch.object(d, 'qbit_info', side_effect=OSError('connection error')):
            with self.assertRaises(OSError):
                self.cleanup()
        with patch.object(d, 'qbit_info', side_effect=[[], OSError('second check')]):
            with self.assertRaises(OSError):
                self.cleanup()
        self.assertEqual(len(list(self.dest.iterdir())), 2)
        self.cleanup()
        self.assertEqual(d.read_json(self.state_path)['status'], 'skipped_duplicate')

    def test_interrupted_deletion_can_resume_using_original_acceptance_time(self):
        original = Path.unlink

        def fail_nfo(path, *args, **kwargs):
            if path == self.dest / 'original.nfo':
                raise OSError('simulated interruption')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_nfo):
            with self.assertRaises(OSError):
                self.cleanup()
        self.assertFalse((self.dest / 'original.mkv').exists())
        self.assertTrue((self.dest / 'original.nfo').exists())
        state = d.read_json(self.state_path)
        self.assertEqual(state['status'], 'dupe_cleanup_started')
        self.assertEqual(state['dupeAcceptedAt'], '2026-09-11T08:50:48Z')
        self.cleanup()
        self.assertFalse(self.dest.exists())

    def test_unsafe_plan_relative_and_external_snapshot_are_rejected(self):
        path = self.spool / 'plan.json'
        plan = d.read_json(path)
        plan['videos'][0]['relative'] = '../outside.mkv'
        d.s.write_json(path, plan)
        with self.assertRaises(ValueError):
            self.cleanup()
        plan['videos'][0]['relative'] = 'original.mkv'
        plan['extras'][0]['snapshot'] = str(self.video)
        d.s.write_json(path, plan)
        with self.assertRaisesRegex(ValueError, 'außerhalb'):
            self.cleanup()
        self.assertEqual(len(list(self.dest.iterdir())), 2)


if __name__ == '__main__':
    unittest.main()
