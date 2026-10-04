"""P1-4: `Project.allowed_domains` is real, validated and enforced.

Before: the field was stored and ignored. Now: validated syntax, per-project Origin policy on widget init/REST/KB/
ticket/WebSocket (re-checked live), CORS/WS handshake following configured domains, and an explicit, documented
decision for Origin-less requests. Origin is never treated as authentication.
"""
import io
from datetime import timedelta  # noqa: F401

from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from config.routing import websocket_urlpatterns
from knowledge_base.tests_base import KBTestMixin
from projects.domains import (
    OriginDecision, all_project_entries, clear_entries_cache, decide_origin, lenient_entries, normalize_entry,
    origin_matches, parse_allowed_domains, parse_origin, sync_allowed_domains,
)
from projects.models import Project
from projects.serializers import ProjectSerializer
from projects.ws_origin import ProjectAwareOriginValidator

SHOP = 'https://shop.example.com'


class ParsingTests(TestCase):
    def test_normalisation(self):
        self.assertEqual(
            parse_allowed_domains('Shop.Example.com, https://shop2.example.com/ ,\n*.Example.org  localhost:3000'),
            ['shop.example.com', 'shop2.example.com', '*.example.org', 'localhost:3000'])

    def test_duplicates_collapse_and_blank_is_empty(self):
        self.assertEqual(parse_allowed_domains('a.com, A.com a.com'), ['a.com'])
        self.assertEqual(parse_allowed_domains(''), [])
        self.assertEqual(parse_allowed_domains(None), [])

    def test_invalid_entries_are_rejected_with_the_offending_value_named(self):
        bad = ['*', '*.com', '*.', 'bad_host', 'user@evil.com', 'shop.example.com/path', 'ftp://x.com', 'x.com:99999',
               'x.com:abc', '-bad.com', 'bad-.com', 'exa_mple.com', '*.1.2', 'http://x.com?q=1', 'x.com#f', '..com', 'https://']
        for entry in bad:
            with self.assertRaises(ValidationError, msg=entry) as ctx:
                parse_allowed_domains(entry)
            self.assertTrue(any(entry in m for m in ctx.exception.messages), ctx.exception.messages)

    def test_one_bad_entry_fails_the_whole_list(self):
        with self.assertRaises(ValidationError):
            parse_allowed_domains('good.example.com, *.com')

    def test_lenient_parse_ignores_bad_entries(self):
        self.assertEqual([e[0] for e in lenient_entries('good.example.com, *.com, bad_host')], ['good.example.com'])


class MatchingTests(TestCase):
    def match(self, origin, domains):
        return origin_matches(origin, lenient_entries(domains))

    def test_exact_host_default_port_only(self):
        self.assertTrue(self.match('https://shop.example.com', 'shop.example.com'))
        self.assertTrue(self.match('http://shop.example.com', 'shop.example.com'))
        self.assertTrue(self.match('https://SHOP.example.com:443', 'shop.example.com'))
        self.assertFalse(self.match('https://shop.example.com:8443', 'shop.example.com'))

    def test_explicit_port(self):
        self.assertTrue(self.match('http://localhost:3000', 'localhost:3000'))
        self.assertFalse(self.match('http://localhost:3001', 'localhost:3000'))
        self.assertFalse(self.match('http://localhost', 'localhost:3000'))

    def test_wildcard_matches_subdomains_but_not_apex_or_lookalikes(self):
        d = '*.example.com'
        self.assertTrue(self.match('https://a.example.com', d))
        self.assertTrue(self.match('https://a.b.example.com', d))
        self.assertFalse(self.match('https://example.com', d))
        self.assertFalse(self.match('https://evilexample.com', d))
        self.assertFalse(self.match('https://example.com.evil.com', d))
        self.assertFalse(self.match('https://a.example.com.evil.com', d))

    def test_other_hosts_and_tricks_never_match(self):
        d = 'shop.example.com'
        for origin in ('https://shop.example.com.evil.com', 'https://evil.com', 'https://xshop.example.com',
                       'https://shop.example.com@evil.com', 'https://evil.com#shop.example.com',
                       'https://evil.com/shop.example.com', 'null', 'NULL', '', None, 'garbage', 'file:///x',
                       'chrome-extension://abc', 'https://', 'javascript:alert(1)'):
            self.assertFalse(self.match(origin, d), origin)

    def test_parse_origin(self):
        self.assertEqual(parse_origin('https://A.com'), ('https', 'a.com', 443))
        self.assertEqual(parse_origin('http://a.com:8080'), ('http', 'a.com', 8080))
        self.assertIsNone(parse_origin('https://a.com/path'))
        self.assertIsNone(parse_origin('null'))


class PolicyTests(KBTestMixin, TestCase):
    def setUp(self):
        self.ws = self.make_workspace()

    def project(self, domains):
        return Project.objects.create(workspace=self.ws, name='p', allowed_domains=domains)

    def test_policy_table(self):
        p = self.project('shop.example.com')
        d = lambda origin, **kw: decide_origin(p, origin, **kw)
        self.assertEqual(d(SHOP, establishing=True), OriginDecision.ALLOWED)
        self.assertEqual(d('https://evil.com', establishing=True), OriginDecision.NOT_ALLOWED)
        self.assertEqual(d('https://evil.com', establishing=False), OriginDecision.NOT_ALLOWED)
        self.assertEqual(d(None, establishing=True), OriginDecision.REQUIRED)       # session creation needs a verifiable origin
        self.assertEqual(d(None, establishing=False), OriginDecision.ALLOWED)       # an authenticated session call may lack one
        self.assertEqual(d('null', establishing=True), OriginDecision.NOT_ALLOWED)  # sandboxed iframes / file:// are not an origin

    def test_unconfigured_project_is_unrestricted_unless_required(self):
        p = self.project('')
        self.assertEqual(decide_origin(p, 'https://anything.com', establishing=True), OriginDecision.ALLOWED)
        self.assertEqual(decide_origin(p, None, establishing=True), OriginDecision.ALLOWED)
        self.assertEqual(decide_origin(p, SHOP, establishing=True, require_domains=True), OriginDecision.NO_DOMAINS)


class ValidationAndStorageTests(KBTestMixin, TestCase):
    def setUp(self):
        self.ws = self.make_workspace()

    def test_model_validation_and_normalisation_on_save(self):
        p = Project(workspace=self.ws, name='p', allowed_domains='*.com')
        with self.assertRaises(ValidationError):
            p.full_clean()
        p = Project.objects.create(workspace=self.ws, name='p', allowed_domains='HTTPS://Shop.Example.com/ ,  a.example.com')
        self.assertEqual(p.allowed_domains, 'shop.example.com, a.example.com')
        p.full_clean()

    def test_serializer_rejects_bad_domains_and_normalises_good_ones(self):
        base = {'name': 'x', 'is_active': True}
        bad = ProjectSerializer(data={**base, 'allowed_domains': 'good.com, *.com'})
        self.assertFalse(bad.is_valid())
        self.assertIn('allowed_domains', bad.errors)
        good = ProjectSerializer(data={**base, 'allowed_domains': 'Good.COM; *.Shop.com'})
        self.assertTrue(good.is_valid(), good.errors)
        self.assertEqual(good.validated_data['allowed_domains'], 'good.com, *.shop.com')

    def test_sync_is_idempotent_and_keeps_manual_entries(self):
        p = Project.objects.create(workspace=self.ws, name='p', allowed_domains='manual.example.com')
        self.assertTrue(sync_allowed_domains(p, ['shop.example.com', 'www.shop.example.com']))
        self.assertFalse(sync_allowed_domains(p, ['shop.example.com']))
        p.refresh_from_db()
        self.assertEqual(p.allowed_domains, 'manual.example.com, shop.example.com, www.shop.example.com')

    def test_union_cache_only_lists_active_projects_in_active_workspaces(self):
        Project.objects.create(workspace=self.ws, name='on', allowed_domains='on.example.com')
        Project.objects.create(workspace=self.ws, name='off', allowed_domains='off.example.com', is_active=False)
        other = self.make_workspace()
        other.is_active = False
        other.save()
        Project.objects.create(workspace=other, name='dead-ws', allowed_domains='deadws.example.com')
        clear_entries_cache()
        self.assertEqual([e[0] for e in all_project_entries()], ['on.example.com'])

    def test_report_command_lists_unconfigured_and_invalid(self):
        Project.objects.create(workspace=self.ws, name='fine', allowed_domains='ok.example.com')
        Project.objects.create(workspace=self.ws, name='empty', allowed_domains='')
        Project.objects.filter(name='fine').update(allowed_domains='ok.example.com, bad_host')
        Project.objects.create(workspace=self.ws, name='off', allowed_domains='', is_active=False)
        out = io.StringIO()
        call_command('report_projects_domain_status', stdout=out)
        text = out.getvalue()
        self.assertIn('1 active project(s) with NO usable allowed_domains', text)
        self.assertIn('"empty"', text)
        self.assertIn('1 active project(s) with some INVALID entries', text)
        self.assertIn('"fine"', text)
        self.assertNotIn('"off"', text)


class _World(KBTestMixin):
    def make_world(self, domains='shop.example.com'):
        self.ws = self.make_workspace()
        self.project = self.make_project(self.ws)
        self.project.allowed_domains = domains
        self.project.save()
        self.visitor = self.make_visitor(self.project)
        self.session = self.make_visitor_session(self.visitor)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)
        self.client = APIClient()
        clear_entries_cache()

    def init(self, origin=None, key=None):
        kw = {'HTTP_ORIGIN': origin} if origin else {}
        return self.client.post('/api/v1/widget/init/', {'project_key': str(key or self.project.public_key)}, format='json', **kw)

    def history(self, origin=None, token=None):
        kw = {'HTTP_ORIGIN': origin} if origin else {}
        return self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                               HTTP_X_WIDGET_SESSION=str(token or self.session.token), **kw)


class RestEnforcementTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_init_from_an_allowed_origin(self):
        self.assertEqual(self.init(SHOP).status_code, 200)

    def test_init_from_a_foreign_origin_is_403(self):
        res = self.init('https://evil.com')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'origin_not_allowed')

    def test_init_without_an_origin_is_refused_when_domains_are_configured(self):
        res = self.init()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'origin_required')

    def test_no_session_is_created_when_refused(self):
        before = self.visitor.__class__.objects.count()
        self.init('https://evil.com')
        self.init()
        self.assertEqual(self.visitor.__class__.objects.count(), before)

    def test_unconfigured_project_keeps_working_and_flag_makes_it_strict(self):
        self.project.allowed_domains = ''
        self.project.save()
        self.assertEqual(self.init('https://anything.com').status_code, 200)
        self.assertEqual(self.init().status_code, 200)
        with override_settings(WIDGET_REQUIRE_ALLOWED_DOMAINS=True):
            res = self.init(SHOP)
            self.assertEqual((res.status_code, res.data['code']), (403, 'no_domains_configured'))

    def test_inactive_project_and_workspace_are_refused(self):
        self.project.is_active = False
        self.project.save()
        self.assertEqual(self.init(SHOP).status_code, 400)
        self.project.is_active = True
        self.project.save()
        self.ws.is_active = False
        self.ws.save()
        self.assertEqual(self.init(SHOP).status_code, 400)

    def test_established_session_calls_check_origin_when_present_and_tolerate_absence(self):
        self.assertEqual(self.history(SHOP).status_code, 200)
        res = self.history('https://evil.com')
        self.assertEqual((res.status_code, res.data['code']), (403, 'origin_not_allowed'))
        self.assertEqual(self.history().status_code, 200)  # same-origin / non-browser call carrying a valid credential

    def test_every_session_endpoint_enforces_origin(self):
        evil = {'HTTP_ORIGIN': 'https://evil.com'}
        cid, tok = self.conv.id, str(self.session.token)
        calls = [
            self.client.post('/api/v1/widget/start/', {'session_token': tok}, format='json', **evil),
            self.client.get(f'/api/v1/widget/conversations/{cid}/messages/', HTTP_X_WIDGET_SESSION=tok, **evil),
            self.client.get(f'/api/v1/widget/conversations/{cid}/branding/', HTTP_X_WIDGET_SESSION=tok, **evil),
            self.client.post(f'/api/v1/widget/conversations/{cid}/mark_read/', {'session_token': tok}, format='json', **evil),
            self.client.post(f'/api/v1/widget/conversations/{cid}/rate/', {'session_token': tok, 'rating': 4}, format='json', **evil),
            self.client.post('/api/v1/widget/ws-ticket/', {'conversation_id': str(cid), 'session_token': tok}, format='json', **evil),
        ]
        for res in calls:
            self.assertEqual(res.status_code, 403, res.request['PATH_INFO'])

    def test_session_of_project_a_is_not_usable_from_project_b_domain(self):
        other = self.make_project(self.ws, name='B')
        other.allowed_domains = 'other.example.com'
        other.save()
        clear_entries_cache()
        self.assertEqual(self.history('https://other.example.com').status_code, 403)

    def test_origin_is_not_authentication(self):
        # a perfectly allowed Origin does not substitute for the credential
        res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/', HTTP_ORIGIN=SHOP)
        self.assertEqual(res.status_code, 401)
        res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/', HTTP_ORIGIN=SHOP,
                              HTTP_X_WIDGET_SESSION='00000000-0000-0000-0000-000000000000')
        self.assertEqual(res.status_code, 401)

    def test_forged_origin_with_a_valid_session_is_still_just_that_sessions_data(self):
        other = self.make_visitor_session(self.make_visitor(self.project))
        res = self.history(SHOP, token=other.token)
        self.assertEqual(res.status_code, 404)  # allowed origin + someone else's session => nothing


class CorsTests(_World, TestCase):
    def setUp(self):
        self.make_world(domains='shop.example.com, *.stores.example.org')

    def preflight(self, path, origin):
        return self.client.options(path, HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
                                   HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,x-widget-session')

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=['https://static.example.net'])
    def test_configured_domains_are_cors_enabled_for_widget_endpoints_only(self):
        res = self.preflight('/api/v1/widget/init/', 'https://shop.example.com')
        self.assertEqual(res.headers.get('Access-Control-Allow-Origin'), 'https://shop.example.com')
        self.assertIn('x-widget-session', res.headers.get('Access-Control-Allow-Headers', '').lower())
        res = self.preflight('/api/v1/widget/init/', 'https://a.stores.example.org')
        self.assertEqual(res.headers.get('Access-Control-Allow-Origin'), 'https://a.stores.example.org')
        # never for the dashboards' API, even for an origin that is configured on a project
        for path in ('/api/v1/auth/login/', '/api/v1/support/', '/api/v1/ws/ticket/'):
            self.assertIsNone(self.preflight(path, 'https://shop.example.com').headers.get('Access-Control-Allow-Origin'), path)

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=['https://static.example.net'])
    def test_unconfigured_origins_stay_blocked_and_static_list_still_works(self):
        self.assertIsNone(self.preflight('/api/v1/widget/init/', 'https://evil.com').headers.get('Access-Control-Allow-Origin'))
        self.assertEqual(self.preflight('/api/v1/widget/init/', 'https://static.example.net').headers.get('Access-Control-Allow-Origin'),
                         'https://static.example.net')
        self.assertIsNone(self.preflight('/api/v1/widget/init/', 'https://stores.example.org').headers.get('Access-Control-Allow-Origin'))

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=['https://static.example.net'])
    def test_inactive_project_domains_are_not_cors_enabled(self):
        self.project.is_active = False
        self.project.save()
        self.assertIsNone(self.preflight('/api/v1/widget/init/', 'https://shop.example.com').headers.get('Access-Control-Allow-Origin'))


# ------------------------------------------------------------------- websockets
_app = ProjectAwareOriginValidator(
    AuthMiddlewareStack(URLRouter(websocket_urlpatterns)), ['https://static.example.net'])


@override_settings(WS_REVALIDATE_SECONDS=0)
class WebSocketOriginTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()

    async def connect(self, origin, path=None):
        headers = [(b'origin', origin.encode())] if origin else []
        comm = WebsocketCommunicator(_app, path or f'/ws/widget/{self.session.token}/{self.conv.id}/', headers=headers)
        connected, _ = await comm.connect()
        return comm, connected

    async def test_configured_domain_is_accepted_at_handshake_and_by_the_project_check(self):
        comm, ok = await self.connect(SHOP)
        self.assertTrue(ok)
        await comm.disconnect()

    async def test_foreign_origin_is_refused_at_the_handshake(self):
        _, ok = await self.connect('https://evil.com')
        self.assertFalse(ok)

    async def test_origin_configured_on_another_project_is_refused_for_this_session(self):
        other = await database_sync_to_async(self.make_project)(self.ws, 'B')
        other.allowed_domains = 'other.example.com'
        await database_sync_to_async(other.save)()
        clear_entries_cache()
        _, ok = await self.connect('https://other.example.com')  # passes the handshake, fails the per-project check
        self.assertFalse(ok)

    async def test_no_origin_is_refused_by_the_static_validator(self):
        _, ok = await self.connect(None)
        self.assertFalse(ok)

    async def test_static_list_origin_must_still_satisfy_the_project_domains(self):
        _, ok = await self.connect('https://static.example.net')  # fine for the handshake, not this project's domain
        self.assertFalse(ok)

    async def test_unconfigured_project_accepts_a_static_list_origin(self):
        def clear():
            self.project.allowed_domains = ''
            self.project.save()
        await database_sync_to_async(clear)()
        comm, ok = await self.connect('https://static.example.net')
        self.assertTrue(ok)
        await comm.disconnect()

    async def test_removing_the_domain_closes_the_open_socket(self):
        comm, ok = await self.connect(SHOP)
        self.assertTrue(ok)

        def retarget():
            self.project.allowed_domains = 'someone-else.example.com'
            self.project.save()
        await database_sync_to_async(retarget)()
        await comm.send_json_to({'message': 'x', 'client_message_id': 'dom1'})
        out = await comm.receive_output(timeout=3)
        self.assertEqual((out['type'], out.get('code')), ('websocket.close', 4403))

    async def test_ticket_socket_is_origin_checked_too(self):
        from conversations.tests_ws_tickets import authenticate, expect_close
        ticket = await database_sync_to_async(
            lambda: APIClient().post('/api/v1/widget/ws-ticket/', {'conversation_id': str(self.conv.id)}, format='json',
                                     HTTP_X_WIDGET_SESSION=str(self.session.token), HTTP_ORIGIN=SHOP).data['ticket'])()
        comm, ok = await self.connect('https://evil.com', path=f'/ws/v2/widget/{self.conv.id}/')
        self.assertFalse(ok)  # refused at the handshake
        comm, ok = await self.connect(SHOP, path=f'/ws/v2/widget/{self.conv.id}/')
        self.assertTrue(ok)
        await authenticate(comm, ticket)
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await comm.disconnect()
