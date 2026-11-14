import copy
import unittest
import sonarr_uppollo as s

class TransitFolderTests(unittest.TestCase):
    def test_videos_and_extras(self):
        plan={'transitFolder':'Release','videos':[{'relative':'Release/Release/ep.mkv','original':'/source/Release/Release/ep.mkv'}]}
        extras=[{'relative':'Release/Subs/de.srt','snapshot':'/saved/sub'}, {'relative':'info.nfo'}]
        self.assertEqual(s.collapse_duplicate_folders(plan,extras),2)
        self.assertEqual(plan['videos'][0]['relative'],'ep.mkv')
        self.assertEqual(plan['videos'][0]['original'],'/source/Release/Release/ep.mkv')
        self.assertEqual(extras[0]['relative'],'Subs/de.srt')
        self.assertEqual(extras[0]['snapshot'],'/saved/sub')
        self.assertEqual(extras[1]['relative'],'info.nfo')

    def test_other_folders_and_case_preserved(self):
        paths=['Episode01/ep.mkv','release/ep.mkv','Subs/de.srt','Other/Release/ep.mkv']
        plan={'transitFolder':'Release','videos':[{'relative':p} for p in paths]}
        self.assertEqual(s.collapse_duplicate_folders(plan,[]),0)
        self.assertEqual([v['relative'] for v in plan['videos']],paths)

    def test_collision_is_atomic(self):
        for paths in [('Release/a.nfo','a.nfo'), ('Release/Subs','Subs/de.srt')]:
            plan={'transitFolder':'Release','videos':[{'relative':paths[0]}]}
            extras=[{'relative':paths[1]}]
            before=copy.deepcopy((plan,extras))
            with self.assertRaises(ValueError): s.collapse_duplicate_folders(plan,extras)
            self.assertEqual((plan,extras),before)

    def test_unsafe_path_rejected(self):
        for path in ['../ep.mkv','/ep.mkv']:
            with self.assertRaises(ValueError):
                s.collapse_duplicate_folders({'transitFolder':'Release','videos':[{'relative':path}]},[])

if __name__=='__main__': unittest.main()
