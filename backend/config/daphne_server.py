"""Daphne entrypoint with a credential-safe access log.

Stock Daphne writes the full request line to its access log, query string included. Signed attachment URLs
(`?sig=...`), the legacy `?session_token=...` and credential-in-path websocket URLs would end up in container
logs. This wrapper keeps the access log (method, path, status, size, client, time) but never the query string
and never the credential path segments that nginx's `rastichat_redacted` format also masks.

Run it exactly like `daphne`:  python -m config.daphne_server -b 0.0.0.0 -p 8000 config.asgi:application
"""
import re
import sys

from daphne import cli
from daphne.access import AccessLogGenerator

_PATH_RULES = (
    (re.compile(r'^(/ws/widget/)[^/]+(/.*)$'), r'\1[redacted]\2'),
    (re.compile(r'^(/ws/dashboard/support/)[^/]+(/.*)$'), r'\1[redacted]\2'),
    (re.compile(r'^(/ws/dashboard/)(?!v2/|support/)[^/]+(/.*)$'), r'\1[redacted]\2'),
    (re.compile(r'^(/ws/notifications/)[^/]+(/?)$'), r'\1[redacted]\2'),
    (re.compile(r'^(/media/(?:kb_)?attachments/)[^?]+$'), r'\1[redacted]'),
)


def redact_request(request: str) -> str:
    """'GET /a/b/?sig=x' -> 'GET /a/b/?[redacted]'; credential path segments become [redacted]."""
    method, sep, target = request.partition(' ')
    if not sep:
        return request
    path, qmark, _query = target.partition('?')
    for pattern, repl in _PATH_RULES:
        path = pattern.sub(repl, path)
    return f'{method} {path}{"?[redacted]" if qmark else ""}'


class RedactingAccessLogGenerator(AccessLogGenerator):
    def write_entry(self, host, date, request, status=None, length=None, ident=None, user=None):
        super().write_entry(host, date, redact_request(request), status, length, ident, user)


def main(argv=None):
    cli.AccessLogGenerator = RedactingAccessLogGenerator
    args = list(sys.argv[1:] if argv is None else argv)
    if '--access-log' not in args:
        args = ['--access-log', '-'] + args  # keep the (now redacted) access log on stdout
    cli.CommandLineInterface().run(args)


if __name__ == '__main__':
    main()
