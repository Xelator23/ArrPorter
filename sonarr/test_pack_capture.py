import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sonarr_uppollo as s

class PackCaptureTests(unittest.TestCase):
    def test_nested_pack_and_removed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Pack'
            episode = root / 'Episode01'
            episode.mkdir(parents=True)
            (root / 'pack.nfo').write_text('original')
            (episode / 'subtitle.srt').write_text('subtitle')
            spool = Path(temp) / 'spool'
            spool.mkdir()
            event = {'sonarr_download_id':'job', 'sonarr_series_id':'100',
                     'sonarr_episodefile_sourcefolder': str(episode)}
            grab = dict(event, sonarr_release_releasetype='SeasonPack', sonarr_release_title='Pack')
            row = {'downloadId':'job','seriesId':100,'outputPath':str(root)}
            config = {'SONARR_URL':'mock','SONARR_API_KEY':'test'}
            with patch.object(s, 'http_json', return_value={'records':[row,row], 'totalRecords':2}):
                self.assertEqual(s.pack_capture_root(config,event,grab),root)
            self.assertTrue(s.capture_extras(root,spool))
            s.shutil.rmtree(root)
            self.assertTrue(s.capture_available(root,spool))
            names = {Path(json.loads(p.read_text())['original']).relative_to(root).as_posix()
                     for p in spool.glob('*.json')}
            self.assertEqual(names, {'pack.nfo','Episode01/subtitle.srt'})
            for bad in [dict(row,downloadId='other'),dict(row,seriesId=999)]:
                with patch.object(s,'http_json',return_value={'records':[bad],'totalRecords':1}):
                    self.assertIsNone(s.pack_capture_root(config,event,grab))
            for bad in [dict(row,outputPath=temp),dict(row,outputPath=str(Path(temp)/'Other'))]:
                with patch.object(s,'http_json',return_value={'records':[bad],'totalRecords':1}):
                    with self.assertRaises(ValueError):
                        s.pack_capture_root(config,event,grab)
            with patch.object(s,'http_json',return_value={'records':[row,dict(row,outputPath=temp)],'totalRecords':2}):
                with self.assertRaises(ValueError):
                    s.pack_capture_root(config,event,grab)

if __name__ == '__main__':
    unittest.main()
