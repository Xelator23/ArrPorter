import unittest
from urllib.error import URLError
import cleanup_support as s


class ErrorDetailTests(unittest.TestCase):
    def test_missing_path_is_shown(self):
        error = FileNotFoundError(2, 'not found', '/data/library/episode.mkv')
        self.assertEqual(s.error_detail(error), 'Datei oder Ordner fehlt: /data/library/episode.mkv')

    def test_network_errors_do_not_expose_request_details(self):
        error = URLError('https://secret:password@example.invalid')
        self.assertEqual(s.error_detail(error), 'URLError')

    def test_validation_errors_remain_readable(self):
        self.assertEqual(s.error_detail(ValueError('Transitdatei fehlt')), 'Transitdatei fehlt')


if __name__ == '__main__':
    unittest.main()
