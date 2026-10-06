"""The Daphne access log must never contain credentials (signed attachment URLs, session tokens, ws tickets)."""
import io
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from django.test import SimpleTestCase

from config.daphne_server import RedactingAccessLogGenerator, redact_request

BACKEND_DIR = Path(__file__).resolve().parent.parent
# built at runtime so that no secret-looking literal sits in the repository (secret scanners flag it, even when fake)
SECRET = '.'.join(['eyJ' + 'tIjoiU0VDUkVU', 'x' * 6 + 'abcDEF123_-'])


class RedactRequestTests(SimpleTestCase):
    def test_query_string_is_dropped(self):
        out = redact_request(f'GET /api/v1/attachments/11111111-2222-3333-4444-555555555555/?sig={SECRET}')
        self.assertEqual(out, 'GET /api/v1/attachments/11111111-2222-3333-4444-555555555555/?[redacted]')
        self.assertNotIn(SECRET, out)

    def test_every_query_parameter_is_dropped(self):
        for q in ('session_token=abc', 'ticket=abc', 'token=abc&x=1', 'x=1'):
            self.assertEqual(redact_request(f'GET /api/v1/x/?{q}'), 'GET /api/v1/x/?[redacted]')

    def test_path_without_query_is_unchanged(self):
        self.assertEqual(redact_request('GET /api/v1/health/'), 'GET /api/v1/health/')
        self.assertEqual(redact_request('WSCONNECT /ws/v2/widget/abc/'), 'WSCONNECT /ws/v2/widget/abc/')

    def test_legacy_credential_in_path_websocket_urls_are_masked(self):
        self.assertEqual(redact_request('WSCONNECTING /ws/widget/SECRETTOKEN/chat/'), 'WSCONNECTING /ws/widget/[redacted]/chat/')
        self.assertEqual(redact_request('WSCONNECTING /ws/dashboard/SECRETJWT/'), 'WSCONNECTING /ws/dashboard/[redacted]/')
        self.assertEqual(redact_request('WSCONNECTING /ws/notifications/SECRETJWT/'), 'WSCONNECTING /ws/notifications/[redacted]/')

    def test_generator_writes_redacted_line_with_observability_fields(self):
        stream = io.StringIO()
        gen = RedactingAccessLogGenerator(stream)
        gen('http', 'complete', {'client': '10.0.0.7:5555', 'method': 'GET', 'path': f'/api/v1/attachments/x/?sig={SECRET}', 'status': 206, 'size': 123})
        line = stream.getvalue()
        self.assertNotIn(SECRET, line)
        self.assertNotIn('sig=', line)
        for part in ('10.0.0.7:5555', 'GET /api/v1/attachments/x/?[redacted]', '206', '123'):
            self.assertIn(part, line)


class DaphneProcessAccessLogTests(SimpleTestCase):
    """End to end: a real daphne_server process answering a request that carries a secret in the query string."""

    def test_real_process_never_logs_the_query_string(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings', 'DEBUG': '1', 'ENVIRONMENT': 'development'}
        proc = subprocess.Popen(
            [sys.executable, '-m', 'config.daphne_server', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=BACKEND_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            for _ in range(60):
                try:
                    socket.create_connection(('127.0.0.1', port), timeout=0.5).close()
                    break
                except OSError:
                    time.sleep(0.5)
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/attachments/x/?sig={SECRET}&session_token=ZZZ', timeout=10)
            except Exception:
                pass  # any status; only the log matters
            time.sleep(1)
        finally:
            proc.terminate()
            out, _ = proc.communicate(timeout=15)
        self.assertNotIn(SECRET, out)
        self.assertNotIn('session_token=', out)
        self.assertNotIn('sig=', out)
        self.assertIn('/api/v1/attachments/x/?[redacted]', out)
