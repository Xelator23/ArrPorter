import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import contextlib
from argparse import Namespace
import sonarr_uppollo as s

def fixture(count=4):
    root = '/downloads/Original Pack (2024)'
    paths = [f'/library/Season 01/Renamed E{i:02}.mkv' for i in range(1, count+1)]
    event = {'sonarr_download_id': 'test-download', 'sonarr_episodefile_episodeids': ','.join(str(i) for i in range(1, count+1)),
             'sonarr_episodefile_paths': '|'.join(paths), 'sonarr_episodefile_count': str(count),
             'sonarr_sourcepath': root, 'sonarr_release_title': 'Series.S01.1080p.WEB-ABJ',
             'sonarr_release_quality': 'WEB-1080p', 'sonarr_episodefile_qualities': '|'.join(['WEB-1080p']*count),
             'sonarr_release_releasetype': 'SeasonPack', 'sonarr_series_tmdbid': '61178',
             'sonarr_series_imdbid': 'tt3127798'}
    records = []
    for i, path in enumerate(paths, 1):
        records.extend([
            {'eventType': 'grabbed', 'episodeId': i, 'downloadId': 'test-download'},
            {'eventType': 'downloadFolderImported', 'episodeId': i, 'downloadId': 'test-download',
             'data': {'droppedPath': f'{root}/original.S01E{i:02}.mkv', 'importedPath': path}}])
    return event, records

class MappingTests(unittest.TestCase):
    def test_pack_preserves_names(self):
        e, r = fixture()
        p = s.make_plan(e, r)
        self.assertEqual(len(p['videos']), 4)
        self.assertEqual(p['transitFolder'], 'Original Pack (2024)')
        self.assertEqual(p['videos'][0]['relative'], 'original.S01E01.mkv')
    def test_single_file_root(self):
        e, r = fixture(1)
        e['sonarr_sourcepath'] = r[1]['data']['droppedPath']
        self.assertEqual(s.make_plan(e, r)['transitFolder'], 'Original Pack (2024)')
    def test_partial_pack(self):
        e, r = fixture()
        e['sonarr_episodefile_episodeids'] = '1,2,3'
        with self.assertRaises(ValueError): s.make_plan(e, r)
    def test_missing_history(self):
        e, r = fixture()
        with self.assertRaises(ValueError): s.make_plan(e, r[:-1])
    def test_other_download_not_merged(self):
        e, r = fixture()
        r[-1]['downloadId'] = 'other'
        with self.assertRaises(ValueError): s.make_plan(e, r)
    def test_no_grab_no_guess(self):
        e, r = fixture()
        with self.assertRaises(ValueError): s.make_plan(e, [x for x in r if x['eventType'] != 'grabbed'])
    def test_ambiguous_original(self):
        e, r = fixture()
        extra = copy.deepcopy(r[-1]); extra['data']['droppedPath'] += '.different'
        with self.assertRaises(ValueError): s.make_plan(e, r + [extra])
    def test_traversal(self):
        e, r = fixture()
        r[-1]['data']['droppedPath'] = e['sonarr_sourcepath'] + '/../outside.mkv'
        with self.assertRaises(ValueError): s.make_plan(e, r)
    def test_duplicate_history_deduplicated(self):
        e, r = fixture()
        self.assertEqual(len(s.make_plan(e, r + [r[-1]])['videos']), 4)
    def test_multi_episode_file(self):
        e, r = fixture(2)
        r[-1]['data'] = r[1]['data'].copy()
        e['sonarr_episodefile_paths'] = r[1]['data']['importedPath']
        e['sonarr_episodefile_count'] = '1'
        self.assertEqual(len(s.make_plan(e, r)['videos']), 1)
    def test_force_tv_and_numeric_tmdb(self):
        e, r = fixture()
        p = s.payload_for(s.make_plan(e,r), e, Path('/transit/pack'), {'API_KEY':'test'})
        self.assertIs(p['ForceTvShow'], True)
        self.assertEqual(p['TMDBID'], 61178)
        self.assertNotIn('TVDBID', p)
    def test_group_and_nfo_filter(self):
        e, r = fixture(); p = s.make_plan(e,r)
        filters = [['resolution', '1080p', 'true'], ['nfo','ABJ']]
        with self.assertRaises(ValueError): s.filter_plan(p,e,filters,[])
        s.filter_plan(p,e,filters,[{'validNfo':True}])
    def test_blocked_tag(self):
        e, r = fixture(); p = s.make_plan(e,r)
        with self.assertRaises(ValueError):
            s.filter_plan(p,e,[['resolution','1080p','true'],['tag','WEB','false']],[])

class CaptureTests(unittest.TestCase):
    def test_empty_source_can_disappear(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'; root.mkdir()
            spool = Path(tmp)/'spool'; spool.mkdir()
            self.assertTrue(s.capture_extras(root,spool))
            root.rmdir()
            self.assertFalse(s.capture_extras(root,spool))
            self.assertTrue(s.capture_available(root,spool))
    def test_extras_survive_source_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'; root.mkdir()
            spool = Path(tmp)/'spool'; spool.mkdir()
            nfo = root/'original.nfo'; nfo.write_text('Original NFO')
            s.capture_extras(root,spool)
            nfo.unlink(); root.rmdir()
            self.assertTrue(s.capture_available(root,spool))
            self.assertEqual(next(spool.glob('*.extra')).read_text(),'Original NFO')
    def test_missing_source_not_assumed_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = Path(tmp)/'spool'; spool.mkdir()
            root = Path(tmp)/'absent'
            self.assertFalse(s.capture_extras(root,spool))
            self.assertFalse(s.capture_available(root,spool))
    def test_subdirectory_does_not_prove_pack_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'; root.mkdir()
            sub = root/'episode'; sub.mkdir()
            spool = Path(tmp)/'spool'; spool.mkdir()
            s.capture_extras(sub,spool)
            self.assertFalse(s.capture_available(root,spool))
    def test_corrupt_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'; root.mkdir()
            spool = Path(tmp)/'spool'; spool.mkdir()
            (root/'original.nfo').write_text('Original')
            s.capture_extras(root,spool)
            next(spool.glob('*.extra')).write_text('Changed')
            with self.assertRaises(ValueError): s.capture_available(root,spool)

class PipelineTests(unittest.TestCase):
    def setUp(self):
        log_patch = patch.object(s, 'LOG_PATH', None)
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def test_repaired_nfo_uses_verified_snapshot_on_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event, records = fixture(1)
            release = event['sonarr_release_title']
            source = root / release
            source.mkdir()
            local = source / 'original.nfo'
            local.write_bytes(b'Original\r\n')
            library = root / 'library'
            library.mkdir()
            video = library / 'renamed.mkv'
            video.write_bytes(b'video')
            upload = root / 'upload'
            upload.mkdir()
            event.update(sonarr_eventtype='Download', sonarr_series_id='1', sonarr_series_path=str(library),
                         sonarr_sourcepath=str(source), sonarr_episodefile_paths=str(video))
            records[1]['data'] = {'droppedPath': str(source / 'original.mkv'), 'importedPath': str(video)}
            base = root / 'transit'
            config = dict(SONARR_URL='mock', SONARR_API_KEY='test', UPPOLLO_URL='mock', API_KEY='test',
                          TRANSIT_BASE=str(base), FILTERS_JSON='[["resolution","1080p","true"]]', CLIENT_UPLOAD_BASE=str(upload))
            args = Namespace(check=False, dry_run=True, no_extras_confirmed=False)
            original = b'Original\n'
            with patch.object(s, 'history', return_value=records), patch.object(s, 'check_indexer'), \
                 patch.object(s, 'check_srrdb', return_value=('srrDB OK', {'original.nfo': original})), \
                 patch.object(s, 'http_json', return_value={'status':'queued'}) as post:
                s.run(config, event, args)
                post.assert_not_called()
                self.assertFalse((base / release).exists())
                self.assertEqual(local.read_bytes(), b'Original\r\n')
                local.unlink()
                source.rmdir()
                args.dry_run = False
                config['DRY_RUN'] = 'false'
                s.run(config, event, args)
                post.assert_called_once()
            extra = json.loads(next((base / '_events').glob('*/plan.json')).read_text())['extras'][0]
            self.assertTrue((base / release / 'original.nfo').samefile(extra['snapshot']))
            self.assertEqual((base / release / 'original.nfo').read_bytes(), original)
            self.assertEqual(extra['sha256'], hashlib.sha256(original).hexdigest())

    def test_real_hardlinks_one_post_and_duplicate_prevention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); library = root/'library'; library.mkdir()
            upload = root/'upload'; upload.mkdir()
            video = library/'renamed.mkv'; video.write_bytes(b'video fixture')
            config = dict(SONARR_URL='mock',SONARR_API_KEY='test',UPPOLLO_URL='mock',API_KEY='test',
                          TRANSIT_BASE=str(root/'transit'),DRY_RUN='false',FILTERS_JSON='[]',CLIENT_UPLOAD_BASE=str(upload))
            event = {'sonarr_eventtype':'Download','sonarr_download_id':'id','sonarr_series_id':'1',
                     'sonarr_series_path':str(library),'sonarr_episodefile_paths':str(video),
                     'sonarr_series_tmdbid':'61178'}
            plan = {'root':str(root/'download'), 'transitFolder':'Original Folder', 'release':'Series.S01-WeebPinn',
                    'releaseType':'SeasonPack','videos':[{'imported':str(video),'relative':'original.mkv'}]}
            args = Namespace(check=False,dry_run=False,no_extras_confirmed=True)
            with patch.object(s,'lock',lambda p:contextlib.nullcontext()), patch.object(s,'history',return_value=[]), \
                 patch.object(s,'make_plan',return_value=plan), patch.object(s,'check_indexer'), patch.object(s,'filter_plan'), \
                 patch.object(s,'http_json',return_value={'status':'queued'}) as post:
                with patch.object(s,'check_srrdb',side_effect=ValueError('srrDB: fehlt: Subs/original.idx')):
                    with self.assertRaisesRegex(ValueError, 'Subs/original.idx'):
                        s.run(config,event,args)
                post.assert_not_called()
                self.assertFalse(list((root/'transit'/'_state').glob('*.json')))
                self.assertFalse((root/'transit'/'original.mkv').exists())
                with patch.object(s,'check_srrdb',return_value=('srrDB OK', {})):
                    s.run(config,event,args)
                self.assertTrue((root/'transit'/'original.mkv').samefile(video))
                self.assertFalse((root/'transit'/'Original Folder').exists())
                self.assertEqual(post.call_args.args[1]['Path'], str(root/'transit'/'original.mkv'))
                s.run(config,event,args)
                self.assertEqual(post.call_count,1)
                state = json.loads(next((root/'transit'/'_state').glob('*.json')).read_text())
                self.assertEqual(state['status'],'accepted_unverified')
                self.assertTrue(state['submittedAt'])

if __name__ == '__main__': unittest.main(verbosity=2)
