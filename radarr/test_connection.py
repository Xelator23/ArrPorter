import ast
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch, Mock
import urllib.error


import uppollo_connection_test as probe


class ProtocolTests(unittest.TestCase):
    def check(self, replies):
        self.calls = []
        def send(url, payload):
            self.calls.append((url, payload))
            return replies[len(self.calls) - 1]
        return probe.check_connection({'UPPOLLO_URL': 'http://uppollo:8080/api/upload', 'API_KEY': 'private-test-key'}, send)

    def test_authenticated_and_no_media_parameters(self):
        result = self.check([(400, probe.EXPECTED), (401, {'error': 'Invalid or missing API key'})])
        self.assertIn('API-Key und API-Berechtigung bestaetigt', result)
        self.assertEqual(self.calls, [('http://uppollo:8080/api/upload/check', {'APIKey': 'private-test-key'}),
                                     ('http://uppollo:8080/api/upload/check', {})])

    def test_disabled_auth_does_not_claim_key_validity(self):
        self.assertIn('Schluesselgueltigkeit nicht pruefbar', self.check([(400, probe.EXPECTED)] * 2))

    def test_failures_never_pass(self):
        for status, body in [(401, {}), (403, {}), (404, {}), (500, {}),
                             (200, {'status': 'queued'}), (400, {}), (400, 'html'),
                             (302, {}), (400, {'error': 'Missing required field', 'hint': 'wrong'})]:
            with self.subTest(status=status, body=body), self.assertRaises(ValueError):
                self.check([(status, body)])
            self.assertEqual(len(self.calls), 1)

    def test_ambiguous_auth_fails(self):
        with self.assertRaisesRegex(ValueError, 'uneindeutig'):
            self.check([(400, probe.EXPECTED), (500, {})])

    def test_bad_urls_are_rejected(self):
        for url in ('', 'file:///api/upload', 'http://user:secret@host/api/upload',
                    'http://host/api/upload?key=secret', 'http://host/api/upload#x',
                    'http://host:bad/api/upload', 'http://host/wrong'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.endpoint(url)
        self.assertEqual(probe.endpoint('https://host/prefix/api/upload/'), 'https://host/prefix/api/upload/check')

    def test_missing_key(self):
        with self.assertRaisesRegex(ValueError, 'API_KEY'):
            probe.check_connection({})

    def test_network_errors_hide_details_and_keys(self):
        for error in (urllib.error.URLError('private-test-key'), TimeoutError('private-test-key')):
            with patch.object(probe.urllib.request, 'build_opener', return_value=Mock(open=Mock(side_effect=error))):
                with self.assertRaises(ValueError) as caught:
                    probe.request('http://localhost/api/upload/check', {'APIKey': 'private-test-key'})
                self.assertIn('nicht erreichbar', str(caught.exception))
                self.assertNotIn('private-test-key', str(caught.exception))

class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append((self.path, payload))
                if self.path == '/redirect/api/upload/check':
                    self.send_response(307)
                    self.send_header('Location', '/leaked')
                    self.end_headers()
                    return
                status = 400 if payload.get('APIKey') == 'local-secret' else 401
                body = probe.EXPECTED if status == 400 else {'error': 'Invalid or missing API key'}
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.requests.clear()

    def test_real_http_without_upload(self):
        result = probe.check_connection({'UPPOLLO_URL': self.url + '/api/upload', 'API_KEY': 'local-secret'})
        self.assertIn('CHECK OK', result)
        self.assertEqual(self.requests, [('/api/upload/check', {'APIKey': 'local-secret'}), ('/api/upload/check', {})])

    def test_real_http_wrong_key(self):
        with self.assertRaisesRegex(ValueError, 'API-Key abgelehnt'):
            probe.check_connection({'UPPOLLO_URL': self.url + '/api/upload', 'API_KEY': 'wrong'})
        self.assertEqual(len(self.requests), 1)

    def test_redirect_not_followed(self):
        with self.assertRaisesRegex(ValueError, 'HTTP 307'):
            probe.check_connection({'UPPOLLO_URL': self.url + '/redirect/api/upload', 'API_KEY': 'local-secret'})
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
