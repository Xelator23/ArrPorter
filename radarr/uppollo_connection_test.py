#!/usr/bin/env python3
"""Probe the upPollo v1.15.1 validation endpoint without any media parameters."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

EXPECTED = {'error': 'Missing required field',
            'hint': "Either 'Path' or 'InfoHash' must be provided"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(upload_url):
    try:
        url = urllib.parse.urlsplit(upload_url)
        if (url.scheme not in ('http', 'https') or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or not url.path.rstrip('/').endswith('/api/upload')):
            raise ValueError
        _ = url.port
    except ValueError:
        raise ValueError('UPPOLLO_URL muss eine HTTP(S)-Upload-URL mit /api/upload sein.') from None
    return urllib.parse.urlunsplit((url.scheme, url.netloc,
                                   url.path.rstrip('/') + '/check', '', ''))


def request(url, payload):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'Arr-upPollo-Connection-Test/1'}, method='POST')
    try:
        try:
            response = opener.open(req, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError('Unerwartet grosse Antwort beim Verbindungstest.')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = None
        return status, body
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ValueError('upPollo nicht erreichbar: Verbindung, DNS, TLS oder Zeitlimit pruefen.') from None


def accepted(status, body):
    return (status == 400 and isinstance(body, dict)
            and all(body.get(key) == value for key, value in EXPECTED.items()))


def check_connection(config, send=request):
    key = config.get('API_KEY', '')
    if not key:
        raise ValueError('Konfiguration fehlt: API_KEY')
    url = endpoint(config.get('UPPOLLO_URL', ''))
    status, body = send(url, {'APIKey': key})
    if not accepted(status, body):
        messages = {401: 'upPollo hat den API-Key abgelehnt.',
                    403: 'upPollo verweigert die API-Berechtigung fuer den Tracker-Benutzer.',
                    404: 'upPollo-Pruef-API nicht gefunden; URL und Version pruefen.',
                    500: 'upPollo meldet einen Serverfehler; Tracker-Zugang und upPollo-Log pruefen.'}
        raise ValueError(messages.get(status, f'Unerwartete upPollo-Antwort (HTTP {status}); Verbindungstest nicht bestaetigt.'))
    # Distinguish a valid key from an API configured to ignore authentication.
    status, body = send(url, {})
    if status == 401 and isinstance(body, dict) and body.get('error') == 'Invalid or missing API key':
        return 'CHECK OK: upPollo erreichbar, API-Key und API-Berechtigung bestaetigt. Kein Upload angelegt.'
    if accepted(status, body):
        return ('CHECK OK: upPollo erreichbar, API-Berechtigung bestaetigt. '
                'HINWEIS: Server akzeptiert Anfragen ohne API-Key; Schluesselgueltigkeit nicht pruefbar. Kein Upload angelegt.')
    raise ValueError(f'upPollo erreichbar, aber API-Key-Pruefung uneindeutig (HTTP {status}); Server-Konfiguration pruefen.')


if __name__ == '__main__':
    try:
        print(check_connection(os.environ))
    except ValueError as exc:
        raise SystemExit('ERROR: ' + str(exc)) from None
