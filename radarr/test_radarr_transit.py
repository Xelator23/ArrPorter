import contextlib
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import radarr_transit as r


class TransitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.base = self.root / 'transit'
        self.movie = self.root / 'movie'
        self.upload = self.root / 'upload'
        for p in (self.base / '_state', self.movie, self.upload):
            p.mkdir(parents=True)
        self.video = self.movie / 'renamed.mkv'
        self.video.write_bytes(b'original video bytes')
        self.manifest = {'destination': str(self.movie), 'files': []}
        self.job = 'a' * 64
        self.env = patch.dict(os.environ, CLIENT_UPLOAD_BASE=str(self.upload), CLIENT_SPECIAL_BASE=str(self.upload))
        self.env.start(); self.addCleanup(self.env.stop)
        if os.name == 'nt':
            mock = patch.object(r, 'locked', lambda p: contextlib.nullcontext())
            mock.start(); self.addCleanup(mock.stop)

    def extra(self, name, content=b'extras'):
        p = self.movie / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        self.manifest['files'].append({'path': str(p), 'sha256': r.digest(p)})
        return str(p)

    def plan(self, group='GROUP', name='original.mkv', nfo='', folder='Release'):
        return r.plan(self.base, self.movie, self.video, name, folder, 'Film.1080p-' + group, self.job, self.manifest, nfo)

    def prepared(self, group='GROUP', nfo=''):
        s = self.plan(group, nfo=nfo)
        r.stage(self.base, s)
        s.update(status='accepted_unverified', submittedAt='2026-09-12T10:00:00Z', updatedAt='2026-09-12T10:00:01Z')
        r.write(self.base / '_state' / (self.job + '.json'), s)
        return s

    def fake(self, s, nested=False):
        root = self.upload / (s['transitFolder'] if nested else Path(s['path']).name)
        target_files = []
        for item in s['files']:
            dst = (root / Path(s['path']).name if nested else root) if s['transitKind'] == 'file' else root / item['relative']
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.link(item.get('snapshot', item['source']), dst)
            target_files.append(dict(name=dst.relative_to(self.upload).as_posix(), size=item['size'], progress=1))
        content = str(dst) if s['transitKind'] == 'file' or len(s['files']) == 1 else str(root)
        torrent = dict(hash='e' * 40, content_path=content, save_path=str(self.upload), progress=1, amount_left=0, state='stalledUP')
        class Fake:
            def get(inner, path):
                return [torrent] if path == '/torrents/info' else target_files
        return Fake(), torrent, target_files

    def test_groups_case_insensitive_exact(self):
        for group in ('snk', 'SNK', 'WeebPinn', 'wEEbpINn', 'DRiFTKiNG', 'driftking'):
            self.assertEqual(self.plan(group)['transitKind'], 'file')
        for group in ('snk2', 'xWeebPinn', 'GROUP', '', 'DRiFTKiNG.extra'):
            self.assertEqual(self.plan(group)['transitKind'], 'directory')

    def test_subtitle_forces_folder(self):
        self.extra('a.idx'); self.extra('a.sub')
        s = self.plan('snk')
        self.assertEqual(s['transitKind'], 'directory')
        self.assertEqual([x['relative'] for x in s['files']], ['original.mkv', 'Subs/a.idx', 'Subs/a.sub'])

    def test_nfo_forces_folder(self):
        nfo = self.extra('release.nfo')
        self.assertEqual(self.plan('snk', nfo=nfo)['transitKind'], 'directory')

    def test_sfv_excluded(self):
        self.extra('checksum.sfv')
        self.assertEqual(len(self.plan('snk')['files']), 1)
        self.assertEqual(self.plan('snk')['transitKind'], 'file')

    def test_mp4_keeps_folder(self):
        self.assertEqual(self.plan('snk', name='original.mp4')['transitKind'], 'directory')

    def test_repeated_folder_and_subs(self):
        nfo = self.extra('Release/Release/original.nfo')
        self.extra('Release/Release/sUbS/de/a.idx')
        self.extra('Other/a.srt')
        s = self.plan(nfo=nfo)
        self.assertEqual([x['relative'] for x in s['files']], ['original.mkv', 'original.nfo', 'Subs/de/a.idx', 'Subs/Other/a.srt'])
        self.assertEqual(r.collapse('release/a.srt', 'Release'), 'release/a.srt')

    def test_collision_before_staging(self):
        self.extra('Release/a.srt'); self.extra('a.srt')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(list(self.base.iterdir()), [self.base / '_state'])

    def test_target_guard_folder(self):
        s = self.plan()
        (self.upload / 'Release').mkdir()
        with self.assertRaises(ValueError):
            r.stage(self.base, s)
        self.assertFalse(Path(s['path']).exists())
        self.assertEqual(list((self.base / '_state').iterdir()), [])

    def test_target_guard_direct_nested(self):
        s = self.plan('snk')
        (self.upload / 'original').mkdir()
        with self.assertRaises(ValueError):
            r.guard(s)

    def test_target_guard_direct_file(self):
        s = self.plan('snk')
        (self.upload / 'original.mkv').write_bytes(b'old')
        with self.assertRaises(ValueError):
            r.guard(s)

    def test_target_base_missing(self):
        self.upload.rmdir()
        with self.assertRaises(ValueError):
            r.guard(self.plan())

    def test_snapshot_survives_library_nfo_upgrade(self):
        nfo = self.extra('release.nfo')
        s = self.prepared(nfo=nfo)
        Path(nfo).unlink(); Path(nfo).write_bytes(b'new release nfo')
        self.assertEqual(len(r.inventory(self.base, s)), 2)

    def test_corrupt_snapshot_rejected(self):
        nfo = self.extra('release.nfo')
        s = self.prepared(nfo=nfo)
        Path(s['files'][1]['snapshot']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            r.inventory(self.base, s)

    def test_unexpected_transit_content_rejected(self):
        s = self.prepared()
        (Path(s['path']) / 'extra.mkv').write_bytes(b'old')
        with self.assertRaises(ValueError):
            r.inventory(self.base, s)

    def test_changed_library_video_rejected(self):
        s = self.prepared()
        self.video.unlink(); self.video.write_bytes(b'replacement')
        with self.assertRaises(ValueError):
            r.inventory(self.base, s)

    def test_unsafe_paths(self):
        for rel in ('../a', '/a', 'a/../b', 'a\\b', '.hidden/a'):
            with self.assertRaises(ValueError):
                r.safe_rel(rel)

    def test_check_then_cleanup_folder(self):
        s = self.prepared()
        q, t, f = self.fake(s)
        r.cleanup(self.base, self.job, qbit=q)
        self.assertTrue(Path(s['path']).exists())
        r.cleanup(self.base, self.job, True, qbit=q)
        self.assertFalse(Path(s['path']).exists())
        self.assertTrue(self.video.exists())
        self.assertTrue((self.upload / 'Release' / 'original.mkv').exists())

    def test_cleanup_direct(self):
        s = self.prepared('snk')
        q, t, f = self.fake(s)
        r.cleanup(self.base, self.job, True, qbit=q)
        self.assertFalse(Path(s['path']).exists())
        self.assertTrue(self.base.is_dir())
        self.assertEqual(r.read(self.base / '_state' / (self.job + '.json'))['status'], 'cleaned')

    def test_cleanup_direct_wrapped_by_uppollo(self):
        s = self.prepared('snk')
        q, t, f = self.fake(s, nested=True)
        r.cleanup(self.base, self.job, True, qbit=q)
        self.assertFalse(Path(s['path']).exists())

    def test_incomplete_torrent_preserved(self):
        s = self.prepared()
        q, t, f = self.fake(s); t['progress'] = .9
        with self.assertRaises(ValueError):
            r.cleanup(self.base, self.job, True, qbit=q)
        self.assertTrue(Path(s['path']).exists())

    def test_wrong_torrent_file_preserved(self):
        s = self.prepared()
        q, t, f = self.fake(s); f[0]['size'] += 1
        with self.assertRaises(ValueError):
            r.cleanup(self.base, self.job, True, qbit=q)

    def test_dupe_cleanup_keeps_backups(self):
        nfo = self.extra('release.nfo')
        s = self.prepared(nfo=nfo)
        class Empty:
            def get(self, endpoint): return []
        r.cleanup(self.base, self.job, True, True, Empty())
        self.assertTrue(Path(s['files'][1]['snapshot']).exists())
        self.assertTrue(self.video.exists())
        self.assertEqual(r.read(self.base / '_state' / (self.job + '.json'))['status'], 'skipped_duplicate')

    def test_dupe_direct_cleanup(self):
        s = self.prepared('snk')
        class Empty:
            def get(self, endpoint): return []
        r.cleanup(self.base, self.job, True, True, Empty())
        self.assertFalse(Path(s['path']).exists())

    def test_qbit_using_transit_blocks_dupe(self):
        s = self.prepared('snk')
        class Busy:
            def get(inner, endpoint):
                return [dict(content_path=s['path'], save_path=str(self.base))]
        with self.assertRaises(ValueError):
            r.cleanup(self.base, self.job, True, True, Busy())

    def test_shared_transit_blocks_cleanup(self):
        s = self.prepared()
        other = dict(s, job='b' * 64)
        r.write(self.base / '_state' / ('b' * 64 + '.json'), other)
        with self.assertRaises(ValueError):
            r.cleanup(self.base, self.job, True, True)

    def test_archived_job_does_not_block_new_cleanup(self):
        s = self.prepared()
        other = dict(s, job='b' * 64, status='archived')
        r.write(self.base / '_state' / ('b' * 64 + '.json'), other)
        q, t, f = self.fake(s)
        r.cleanup(self.base, self.job, True, qbit=q)
        self.assertFalse(Path(s['path']).exists())

    def test_old_inventory_rejected(self):
        with self.assertRaises(ValueError):
            r.inventory(self.base, {'path': str(self.base / 'Release')})

    def test_legacy_dupe_reports_manual_without_changes(self):
        s = self.prepared()
        s.pop('job'); s.pop('manifestVersion'); s.pop('files')
        file = self.base / '_state' / (self.job + '.json')
        r.write(file, s)
        before = file.read_bytes()
        event = dict(path=s['path'], start=r.epoch(s['submittedAt']), finish=r.epoch(s['updatedAt']) + 1)
        output = io.StringIO()
        with patch.dict(os.environ, TRANSIT_BASE=str(self.base)), patch.object(r.sys, 'argv', ['test', 'duplicates', '--apply', str(event['finish'] + 10)]), patch.object(r, 'dupe_events', return_value=([event], [(s['path'], event['start'])])), patch.object(r, 'cleanup') as cleanup, contextlib.redirect_stdout(output):
            r.main()
        cleanup.assert_not_called()
        self.assertIn('DUPE MANUELL:', output.getvalue())
        self.assertEqual(file.read_bytes(), before)
        self.assertTrue(Path(s['path']).exists())

    def test_new_inventory_missing_job_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Falsche Job-Zuordnung'):
            r.supports_cleanup({'manifestVersion': 1}, self.job)

    def test_mismatched_job_still_rejected(self):
        for version in (None, 1):
            with self.assertRaisesRegex(ValueError, 'Falsche Job-Zuordnung'):
                r.supports_cleanup({'manifestVersion': version, 'job': 'b' * 64}, self.job)

    def test_interrupted_cleanup_resumes(self):
        nfo = self.extra('release.nfo')
        s = self.prepared(nfo=nfo)
        q, t, f = self.fake(s)
        s['status'] = 'cleanup_started'
        r.write(self.base / '_state' / (self.job + '.json'), s)
        (Path(s['path']) / 'original.mkv').unlink()
        r.cleanup(self.base, self.job, True, qbit=q)
        self.assertFalse(Path(s['path']).exists())

    def test_missing_file_before_cleanup_rejected(self):
        s = self.prepared()
        (Path(s['path']) / 'original.mkv').unlink()
        with self.assertRaises(ValueError):
            r.inventory(self.base, s)

    def test_stage_does_not_merge_existing_transit(self):
        s = self.plan()
        Path(s['path']).mkdir()
        (Path(s['path']) / 'old.mkv').write_bytes(b'old')
        with self.assertRaises(ValueError):
            r.stage(self.base, s)
        self.assertEqual([p.name for p in Path(s['path']).iterdir()], ['old.mkv'])

    def test_direct_target_wrong_content_rejected(self):
        s = self.prepared('snk')
        q, t, f = self.fake(s)
        dst = Path(t['content_path'])
        dst.unlink(); dst.write_bytes(b'X' * s['files'][0]['size'])
        with self.assertRaises(ValueError):
            r.cleanup(self.base, self.job, True, qbit=q)
        self.assertTrue(Path(s['path']).exists())

    @unittest.skipIf(os.name == 'nt', 'Windows requires separate symlink privileges; run in Linux container')
    def test_symlink_transit_rejected(self):
        s = self.prepared()
        (Path(s['path']) / 'unexpected').symlink_to(self.movie, target_is_directory=True)
        with self.assertRaises(ValueError):
            r.inventory(self.base, s)

    @unittest.skipIf(os.name == 'nt', 'Windows requires separate symlink privileges; run in Linux container')
    def test_symlink_target_rejected(self):
        s = self.plan()
        (self.upload / 'Release').symlink_to(self.root / 'missing', target_is_directory=True)
        with self.assertRaises(ValueError):
            r.guard(s)


class LogTests(unittest.TestCase):
    def lines(self, messages):
        return [f'time=2026-09-12T10:00:{i:02d}Z level=INFO msg={json.dumps(m, ensure_ascii=False)}\n' for i, m in enumerate(messages)]

    def example(self):
        return ['New Upload: /transit/Release', 'found dupe: name', 'RocketHD: skipped · dupe found', 'Error during upload processing: all tracker uploads failed']

    def test_explicit_dupe(self):
        events, starts = r.dupe_events(self.lines(self.example()))
        self.assertEqual(len(events), 1)
        s = dict(path='/transit/Release', submittedAt='2026-09-12T10:00:00Z', updatedAt='2026-09-12T10:00:01Z')
        self.assertTrue(r.dupe_matches(s, events, starts, r.epoch('2026-09-12T10:01:00Z')))
        self.assertFalse(r.dupe_matches(s, events, starts, r.epoch('2026-09-12T10:00:03Z')))

    def test_missing_start_or_finish(self):
        for messages in (self.example()[1:], self.example()[:-1]):
            self.assertEqual(r.dupe_events(self.lines(messages))[0], [])

    def test_dupe_candidates_not_confirmation(self):
        messages = self.example(); messages[1] = 'Found 13 dupe candidates:'
        self.assertEqual(r.dupe_events(self.lines(messages))[0], [])

    def test_other_error_not_dupe(self):
        messages = self.example(); messages[-1] = 'Error during upload processing: timeout'
        self.assertEqual(r.dupe_events(self.lines(messages))[0], [])

    def test_overlap_rejected(self):
        messages = self.example(); messages.insert(1, 'New Upload: /transit/Other')
        self.assertEqual(r.dupe_events(self.lines(messages))[0], [])

    def test_later_submission_rejected(self):
        messages = self.example() + ['New Upload: /transit/Release']
        events, starts = r.dupe_events(self.lines(messages))
        s = dict(path='/transit/Release', submittedAt='2026-09-12T10:00:00Z', updatedAt='2026-09-12T10:00:01Z')
        self.assertFalse(r.dupe_matches(s, events, starts, r.epoch('2026-09-12T10:01:00Z')))

    def test_truncated_log_rejected(self):
        lines = self.lines(self.example()); lines.insert(2, 'truncated\n')
        self.assertEqual(r.dupe_events(lines)[0], [])

    def test_success_not_dupe(self):
        messages = self.example(); messages[-1] = 'Upload processed successfully'
        self.assertEqual(r.dupe_events(self.lines(messages))[0], [])


if __name__ == '__main__':
    unittest.main()
