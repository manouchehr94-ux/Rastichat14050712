"""Regression tests for the staging/production fail-fast guardrails in
config/settings.py — proving each invalid-configuration scenario actually
aborts startup, not just that the code LOOKS like it should. Settings are
loaded once per Python process and cached by the import system, so the only
reliable way to test "does a given env var combination make Django refuse
to start" is a real subprocess per scenario (`manage.py check`), not
in-process mocking of `os.environ` + re-importing config.settings.

`manage.py check` never needs a live DB/Redis connection (confirmed by
direct testing — Django's system check framework is static configuration
analysis, no runtime queries) so DB_HOST/REDIS_HOST below point at hosts
that don't exist; only the guardrail logic under test is being exercised.
"""
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent

VALID_STAGING_ENV = {
    'ENVIRONMENT': 'staging',
    'DEBUG': '0',
    # Obviously-fake but still long/varied enough (67 chars, 19 unique) to
    # clear security.W009's threshold, unlike a short or repetitive fixed
    # string a CI config might otherwise pick by accident. Deliberately
    # NOT random-looking (a real generated secret would be) so a secret
    # scanner reading this file doesn't mistake a test fixture for a real
    # credential.
    'DJANGO_SECRET_KEY': 'this-is-a-fake-test-only-secret-key-value-not-a-real-credential-000',  # gitleaks:allow
    'ALLOWED_HOSTS': 'chat-staging.example.com',
    'CSRF_TRUSTED_ORIGINS': 'https://chat-staging.example.com',
    'CORS_ALLOWED_ORIGINS': 'https://chat-staging.example.com',
    'DB_HOST': 'db-host-does-not-need-to-exist-for-check.invalid',
    'DB_USER': 'rastichat',
    'DB_PASSWORD': 'irrelevant-for-check',
    'DB_NAME': 'rastichat_db',
    'REDIS_HOST': 'redis-host-does-not-need-to-exist-for-check.invalid',
    'MONITORING_TOKEN': 'test-monitoring-token-not-real',
}


def run_check(env_overrides, extra_args=None):
    """Runs `manage.py check` (or `check --deploy --fail-level WARNING` if
    extra_args given) in a fresh subprocess with VALID_STAGING_ENV merged
    with env_overrides (a key set to None removes it entirely, simulating
    "not set" rather than "set to empty string", which settings.py's
    _env_list/_env_bool treat differently).
    """
    import os
    env = {'PATH': os.environ.get('PATH', '')}
    for k, v in VALID_STAGING_ENV.items():
        env[k] = v
    for k, v in env_overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    args = [sys.executable, 'manage.py', 'check'] + (extra_args or [])
    return subprocess.run(args, cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=60)


class StagingFailFastTests(SimpleTestCase):
    """Every one of these must FAIL (non-zero exit) — each represents a
    single invalid-configuration scenario that must never be allowed to
    serve real traffic.
    """

    def test_baseline_valid_config_passes(self):
        # Sanity check the fixture itself is valid before trusting any of
        # the "this specific thing being wrong is what fails it" tests
        # below — otherwise a broken baseline could make every negative
        # test below pass for the wrong reason.
        result = run_check({})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_secret_key_fails(self):
        result = run_check({'DJANGO_SECRET_KEY': None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', result.stderr)

    def test_debug_true_fails(self):
        result = run_check({'DEBUG': '1'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DEBUG', result.stderr)

    def test_wildcard_allowed_hosts_fails(self):
        result = run_check({'ALLOWED_HOSTS': '*'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ALLOWED_HOSTS', result.stderr)

    def test_missing_allowed_hosts_fails(self):
        result = run_check({'ALLOWED_HOSTS': None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ALLOWED_HOSTS', result.stderr)

    def test_missing_csrf_trusted_origins_fails(self):
        result = run_check({'CSRF_TRUSTED_ORIGINS': None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CSRF_TRUSTED_ORIGINS', result.stderr)

    def test_missing_cors_allowed_origins_fails(self):
        result = run_check({'CORS_ALLOWED_ORIGINS': None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CORS_ALLOWED_ORIGINS', result.stderr)

    def test_invalid_environment_value_fails(self):
        result = run_check({'ENVIRONMENT': 'not-a-real-environment'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ENVIRONMENT', result.stderr)

    def test_missing_monitoring_token_fails(self):
        result = run_check({'MONITORING_TOKEN': None})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('MONITORING_TOKEN', result.stderr)


class DeployTimeSecurityCheckTests(SimpleTestCase):
    """`check --deploy --fail-level WARNING --tag security` — the exact
    command docker-entrypoint.sh's `check-deploy` subcommand runs (and
    therefore scripts/staging/deploy.sh's Step 6 and CI's docker-build
    job). Three things this test class exists to prove concretely, not
    just assert from documentation:

    1. Plain `check --deploy` (no --fail-level) is NOT enough: Django only
       exits non-zero on ERROR-level findings by default, and a weak
       SECRET_KEY is only ever a WARNING (security.W009).
    2. `--fail-level WARNING` ALONE is not safely usable either: it also
       catches drf_spectacular's unrelated API-schema-generation warnings
       (present on every run regardless of environment) and
       security.W021 (HSTS preload, a deliberate opt-in) — both of which
       would make this gate permanently red. `--tag security` plus
       config/settings.py's SILENCED_SYSTEM_CHECKS close that gap.
    3. With both fixes applied, a weak secret still fails and a valid one
       still passes cleanly.
    """

    def test_baseline_valid_config_passes_with_fail_level_warning(self):
        result = run_check({}, extra_args=['--deploy', '--fail-level', 'WARNING', '--tag', 'security'])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_weak_secret_key_fails_with_fail_level_warning(self):
        result = run_check(
            {'DJANGO_SECRET_KEY': 'x'},
            extra_args=['--deploy', '--fail-level', 'WARNING', '--tag', 'security'],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('security.W009', result.stderr)

    def test_weak_secret_key_passes_plain_check_deploy_without_fail_level(self):
        # This is the gap the deploy-time check exists to close — documents
        # it with a real assertion so nobody "fixes" check-deploy back to
        # plain `check --deploy` without noticing this test explains why
        # that would silently reopen the gap.
        result = run_check({'DJANGO_SECRET_KEY': 'x'}, extra_args=['--deploy'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('security.W009', result.stdout + result.stderr)

    def test_fail_level_warning_without_tag_security_is_permanently_red(self):
        # Documents WHY --tag security is required alongside --fail-level
        # WARNING, with a real assertion rather than a comment someone
        # could silently invalidate later: even a fully valid staging
        # config fails here, on findings that have nothing to do with
        # this deploy's actual security posture (drf_spectacular's schema
        # warnings are structural, not environment-dependent).
        result = run_check({}, extra_args=['--deploy', '--fail-level', 'WARNING'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('drf_spectacular', result.stderr)
