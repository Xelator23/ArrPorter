import tempfile
from pathlib import Path
import unittest
import sonarr_uppollo as s

class TargetTests(unittest.TestCase):
    def test_existing_target_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'CLIENT_UPLOAD_BASE':temp}
            plan={'release':'Release-GROUP','transitFolder':'Release'}
            s.require_empty_upload_target(config,plan)
            target=Path(temp)/'Release'; target.mkdir()
            with self.assertRaises(ValueError): s.require_empty_upload_target(config,plan)
            target.rmdir(); target.write_text('old')
            with self.assertRaises(ValueError): s.require_empty_upload_target(config,plan)
    def test_missing_base_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                s.require_empty_upload_target({'CLIENT_UPLOAD_BASE':str(Path(temp)/'missing')}, {'release':'R-G','transitFolder':'R'})
if __name__=='__main__': unittest.main()
