import json
from pathlib import Path
import tempfile
import unittest
import radarr_extras as r

class ExtrasTests(unittest.TestCase):
    def test_upgrade_and_no_old_subtitles_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); src=base/'download'; dst=base/'movie'
            src.mkdir(); dst.mkdir(); manifest=base/'manifest.json'
            (dst/'wvf.nfo').write_text('old')
            (dst/'old.srt').write_text('old sub')
            (src/'warezcx.nfo').write_text('new')
            (src/'Subs').mkdir(); (src/'Subs'/'de.srt').write_text('new sub')
            r.sync(src,dst,manifest)
            self.assertFalse((dst/'wvf.nfo').exists())
            self.assertEqual((dst/'warezcx.nfo').read_text(),'new')
            self.assertTrue((dst/'old.srt').exists())
            files=json.loads(manifest.read_text())['files']
            self.assertEqual({Path(x['path']).relative_to(dst).as_posix() for x in files}, {'warezcx.nfo','Subs/de.srt'})
            r.sync(src,dst,manifest)

    def test_same_name_nfo_and_empty_download(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); src=base/'src'; dst=base/'dst'; src.mkdir(); dst.mkdir()
            (dst/'release.nfo').write_text('old'); (src/'release.nfo').write_text('new')
            r.sync(src,dst,base/'manifest')
            self.assertEqual((dst/'release.nfo').read_text(),'new')
            (src/'release.nfo').unlink()
            r.sync(src,dst,base/'manifest')
            self.assertFalse((dst/'release.nfo').exists())

    def test_missing_source_and_conflict_preserve_old_nfo(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); src=base/'src'; dst=base/'dst'; dst.mkdir()
            (dst/'old.nfo').write_text('old')
            with self.assertRaises(ValueError): r.sync(src,dst,base/'manifest')
            self.assertTrue((dst/'old.nfo').exists())
            src.mkdir(); (src/'sub.srt').write_text('new'); (dst/'sub.srt').write_text('old')
            with self.assertRaises(ValueError): r.sync(src,dst,base/'manifest')
            self.assertTrue((dst/'old.nfo').exists())

if __name__ == '__main__': unittest.main()
