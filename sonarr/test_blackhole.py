import unittest
from unittest.mock import patch
import sonarr_uppollo as s
from test_sonarr import fixture

class BlackholeTests(unittest.TestCase):
    def setUp(self):
        log_patch = patch.object(s, 'LOG_PATH', None)
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def test_complete_without_grab_and_partial_rejected(self):
        event, records = fixture()
        event.update(sonarr_download_client_type='Usenet Blackhole', sonarr_series_id='1', sonarr_episodefile_seasonnumber='1')
        records=[r for r in records if r['eventType'] != 'grabbed']
        episodes=[{'id':i,'seasonNumber':1} for i in range(1,5)]
        config={'SONARR_URL':'mock','SONARR_API_KEY':'test'}
        with patch.object(s,'http_json',return_value=episodes):
            expected=s.blackhole_expected(config,event)
            self.assertEqual(len(s.make_plan(event,records,expected)['videos']),4)
            event.pop('sonarr_release_title', None)
            plan = s.make_plan(event, records, expected)
            self.assertEqual(plan['release'], plan['transitFolder'])
            event['sonarr_episodefile_episodeids']='1,2,3'
            with self.assertRaises(ValueError): s.blackhole_expected(config,event)

    def test_sources_and_existing_clients(self):
        event={'sonarr_download_client_type':'Usenet Blackhole'}
        s.check_indexer(event,{},[['indexer','blocked']])
        for client in ['SABnzbd', 'NZBGet']:
            with self.assertRaises(ValueError):
                s.check_indexer({'sonarr_download_client_type':client,
                                 'sonarr_release_indexer':'blocked'}, {}, [['indexer','blocked']])
        with self.assertRaises(ValueError):
            s.check_indexer({'sonarr_download_client_type':'qBittorrent', 'sonarr_download_id':'invalid'}, {}, [])
        event,records=fixture()
        records=[r for r in records if r['eventType']!='grabbed']
        with self.assertRaises(ValueError): s.make_plan(event,records)

if __name__=='__main__': unittest.main()
