import unittest
from sonarr_uppollo import select_transit_layout


class Layout(unittest.TestCase):
    def test_groups_and_extras(self):
        for suffix, extras, kind in [
            ('pUDDing-SAMA', [], 'file'), ('TOONSHUB', [], 'file'),
            ('pudding-sama-extra', [], 'directory'), ('Group', [], 'directory'),
            ('Pudding-sama', ['sub.idx'], 'directory')]:
            with self.subTest(group=suffix, extras=extras):
                plan = {'release': 'Example.S01E01-' + suffix, 'transitFolder': 'Example',
                        'videos': [{'relative': 'original.mkv'}]}
                select_transit_layout(plan, extras)
                self.assertEqual(plan['transitKind'], kind)
