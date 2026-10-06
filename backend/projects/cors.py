"""CORS for the public widget endpoints follows the projects' configured domains.

The global CORS allow-list (`CORS_ALLOWED_ORIGINS`) is a static environment value; a SaaS storefront on a customer's
own domain would otherwise need an operator to edit it and redeploy. Preflights carry no project key or session, so
the per-project check cannot run there — instead an origin is CORS-enabled for the widget/KB-public endpoints when it
is configured on ANY active project. This only lets the browser *read* the response; every real request is still
authenticated (session / ticket) and checked against ITS project's domains (`visitors.sessions.enforce_project_origin`).
Dashboard/admin endpoints are never opened up this way.
"""
from corsheaders.signals import check_request_enabled
from django.dispatch import receiver

from .domains import all_project_entries, origin_matches

# `/identity/customer/` is the browser-side half of the trusted-customer bootstrap (the page's script exchanges the host-signed assertion for a
# visitor session): it must be reachable from the host's own domain exactly like the widget endpoints. It is safe for the same reason — the request
# is authenticated by the assertion and refused unless its Origin is one of THE PROJECT'S configured domains (`enforce_project_origin`). The staff
# exchange (`/identity/staff/`) is called from the dashboards, which are on the static allow-list, so it is deliberately NOT opened up here.
WIDGET_PATH_PREFIXES = ('/api/v1/widget/', '/api/v1/kb/public/', '/api/v1/identity/customer/')


@receiver(check_request_enabled)
def allow_configured_project_domains(sender, request, **kwargs):
    if not request.path.startswith(WIDGET_PATH_PREFIXES):
        return False
    return origin_matches(request.headers.get('Origin'), all_project_entries())
