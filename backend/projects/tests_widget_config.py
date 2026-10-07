import copy

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent
from conversations.models import Conversation, PreChatSubmission
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership

from .models import Project, ProjectWidgetConfig
from .widget_config import ConfigError, DEFAULTS, resolve_config, validate_answers, validate_config

User = get_user_model()

FORM = {
    'pre_chat': {'enabled': True, 'title': 'قبل از شروع', 'fields': [
        {'key': 'topic', 'type': 'select', 'label': 'موضوع', 'required': True, 'order': 1,
         'choices': [{'value': 'order', 'label': 'سفارش'}, {'value': 'billing', 'label': 'پرداخت'}]},
        {'key': 'order_no', 'type': 'text', 'label': 'شماره سفارش', 'order': 2, 'max_length': 20},
        {'key': 'email', 'type': 'email', 'label': 'ایمیل', 'order': 3},
        {'key': 'phone', 'type': 'phone', 'label': 'تلفن', 'order': 4},
        {'key': 'details', 'type': 'textarea', 'label': 'توضیحات', 'order': 5},
        {'key': 'agree', 'type': 'consent', 'label': 'قوانین را می‌پذیرم', 'required': True, 'order': 6},
        {'key': 'page', 'type': 'hidden', 'label': '', 'order': 7},
    ]},
}


def errors_of(doc):
    try:
        validate_config(doc)
    except ConfigError as exc:
        return exc.errors
    return {}


class SchemaTests(SimpleTestCase):
    def test_defaults_are_valid_and_complete(self):
        self.assertEqual(validate_config({'version': 1})['version'], 1)
        self.assertEqual(validate_config(copy.deepcopy(DEFAULTS))['launcher']['mode'], 'icon')

    def test_launcher_options(self):
        doc = {'launcher': {'enabled': True, 'mode': 'icon_text', 'position': 'bottom-left', 'offset': {'x': 8, 'y': 90},
                            'label': 'کمک', 'tooltip': 'گفتگو', 'icon': 'headset', 'color': '#336699',
                            'greeting': 'سلام!', 'auto_open': True, 'mobile': {'fullscreen': False}}}
        out = validate_config(doc)
        self.assertEqual(out['launcher']['color'], '#336699')
        self.assertFalse(out['launcher']['mobile']['fullscreen'])

    def test_rejections(self):
        cases = {
            'launcher.mode': {'launcher': {'mode': 'banner'}},
            'launcher.position': {'launcher': {'position': 'top'}},
            'launcher.color': {'launcher': {'color': 'red'}},
            'launcher.icon': {'launcher': {'icon': 'skull'}},
            'launcher.offset.x': {'launcher': {'offset': {'x': 9999}}},
            'launcher.label': {'launcher': {'label': 'x' * 41}},
            'launcher.greeting': {'launcher': {'greeting': '<script>alert(1)</script>'}},
            'launcher.sneaky': {'launcher': {'sneaky': 1}},
            'bogus': {'bogus': {}},
            'version': {'version': 2},
            'behavior.start_mode': {'behavior': {'start_mode': 'whenever'}},
            'locale': {'locale': 'fr'},
            'visibility.hide_on_paths[0]': {'visibility': {'hide_on_paths': ['https://evil']}},
            'identity': {'identity': {'guest_allowed': True, 'authenticated_only': True}},
            'capabilities.attachments': {'capabilities': {'attachments': 'yes'}},
        }
        for path, doc in cases.items():
            self.assertIn(path, errors_of(doc), path)

    def test_field_rules(self):
        def with_fields(*fields, enabled=True):
            return {'pre_chat': {'enabled': enabled, 'fields': list(fields)}}
        text = {'key': 'q', 'type': 'text', 'label': 'Q'}
        self.assertEqual(errors_of(with_fields(text)), {})
        self.assertIn('pre_chat.fields[1].key', errors_of(with_fields(text, dict(text))))              # duplicate
        self.assertIn('pre_chat.fields[0].key', errors_of(with_fields({**text, 'key': 'Bad Key'})))
        self.assertIn('pre_chat.fields[0].type', errors_of(with_fields({**text, 'type': 'file'})))
        self.assertIn('pre_chat.fields[0].choices', errors_of(with_fields({'key': 's', 'type': 'select', 'label': 'S'})))
        self.assertIn('pre_chat.fields[0].choices', errors_of(with_fields({**text, 'choices': [{'value': 'a', 'label': 'A'}]})))
        self.assertIn('pre_chat.fields[0].label', errors_of(with_fields({'key': 'c', 'type': 'consent', 'label': ''})))
        self.assertIn('pre_chat.fields[0].max_length', errors_of(with_fields({**text, 'max_length': 99999})))
        self.assertIn('pre_chat.fields', errors_of(with_fields(enabled=True)))                          # enabled, no question
        self.assertIn('pre_chat.fields', errors_of(with_fields({'key': 'h', 'type': 'hidden', 'label': ''})))  # hidden only
        self.assertIn('pre_chat.fields', errors_of({'pre_chat': {'fields': [text] * 13}}))
        self.assertIn('pre_chat.fields[0].sneaky', errors_of(with_fields({**text, 'sneaky': 1})))

    def test_no_free_form_regex_is_accepted(self):
        self.assertIn('pre_chat.fields[0].pattern', errors_of(
            {'pre_chat': {'fields': [{'key': 'q', 'type': 'text', 'label': 'Q', 'pattern': '(a+)+$'}]}}))

    def test_one_question_mode_is_just_configuration(self):
        doc = {'pre_chat': {'enabled': True, 'fields': [{'key': 'help', 'type': 'text', 'label': 'چه کمکی می‌توانیم بکنیم؟',
                                                          'required': True}]}}
        self.assertEqual(errors_of(doc), {})


class AnswerValidationTests(SimpleTestCase):
    def config(self):
        return resolve_config_from(FORM)

    def test_valid_answers_snapshot_labels_and_sources(self):
        snap = validate_answers(self.config(), {'topic': 'billing', 'order_no': ' A-1 ', 'agree': True, 'page': '/cart',
                                                'email': 'a@b.co'})
        by_key = {a['key']: a for a in snap}
        self.assertEqual(by_key['topic']['label'], 'موضوع')
        self.assertEqual(by_key['order_no']['value'], 'A-1')
        self.assertEqual(by_key['page']['source'], 'client')
        self.assertEqual(by_key['topic']['source'], 'visitor')
        self.assertNotIn('phone', by_key)

    def test_invalid_answers(self):
        cfg = self.config()
        cases = {
            'topic': [{'agree': True}, {'topic': 'nope', 'agree': True}, {'topic': ['order'], 'agree': True}],
            'agree': [{'topic': 'order'}, {'topic': 'order', 'agree': False}],
            'email': [{'topic': 'order', 'agree': True, 'email': 'not-an-email'}],
            'phone': [{'topic': 'order', 'agree': True, 'phone': 'abc'}],
            'order_no': [{'topic': 'order', 'agree': True, 'order_no': 'x' * 21}],
            'extra': [{'topic': 'order', 'agree': True, 'extra': 'x'}],
            'details': [{'topic': 'order', 'agree': True, 'details': 'x' * 2001}],
        }
        for key, answer_sets in cases.items():
            for answers in answer_sets:
                with self.assertRaises(ConfigError, msg=answers) as ctx:
                    validate_answers(cfg, answers)
                self.assertIn(key, ctx.exception.errors)

    def test_non_object_answers(self):
        with self.assertRaises(ConfigError):
            validate_answers(self.config(), ['topic'])


def resolve_config_from(doc):
    class P:
        widget_config = type('W', (), {'config': validate_config({**doc})})()
    return resolve_config(P())


class Fixture(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P')
        self.ws_a = Workspace.objects.create(name='A', platform=self.platform)
        self.ws_b = Workspace.objects.create(name='B', platform=self.platform)
        self.proj_a = Project.objects.create(name='Shop A', workspace=self.ws_a, logo_url='https://x.test/l.png', subtitle='sub')
        self.proj_b = Project.objects.create(name='Shop B', workspace=self.ws_b)
        self.admin_a = self.user('admin-a', self.ws_a, 'WORKSPACE_ADMIN')
        self.admin_b = self.user('admin-b', self.ws_b, 'WORKSPACE_ADMIN')
        self.op_a = self.user('op-a', self.ws_a, 'WORKSPACE_OPERATOR')
        # the dangerous combination: admin of A, merely operator of B
        self.mixed = self.user('mixed', self.ws_a, 'WORKSPACE_ADMIN')
        WorkspaceMembership.objects.create(user=self.mixed, workspace=self.ws_b, role='WORKSPACE_OPERATOR')

    def user(self, name, ws, role):
        u = User.objects.create_user(email=f'{name}@t.com', password='pass1234')
        WorkspaceMembership.objects.create(user=u, workspace=ws, role=role)
        return u

    def as_user(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def save_config(self, project, doc):
        ProjectWidgetConfig.objects.update_or_create(project=project, defaults={'config': validate_config(doc)})

    def guest(self, project):
        res = self.client.post('/api/v1/widget/init/', {'project_key': str(project.public_key)}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['session_token']

    def start(self, token, **body):
        return self.client.post('/api/v1/widget/start/', body, format='json', HTTP_X_WIDGET_SESSION=token)


class PublicConfigApiTests(Fixture):
    def get(self, project, **extra):
        return self.client.get('/api/v1/widget/config/', {'project_key': str(project.public_key)}, **extra)

    def test_project_without_stored_config_gets_the_legacy_compatible_defaults(self):
        res = self.get(self.proj_a)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['version'], 1)
        self.assertEqual(body['behavior']['start_mode'], 'on_load')
        self.assertTrue(body['launcher']['enabled'])
        self.assertFalse(body['pre_chat']['enabled'])
        self.assertEqual(body['project'], {'key': str(self.proj_a.public_key), 'name': 'Shop A',
                                           'logo_url': 'https://x.test/l.png', 'subtitle': 'sub'})
        self.assertEqual((body['locale'], body['direction']), ('fa', 'rtl'))

    def test_response_exposes_no_internal_identifiers(self):
        text = self.get(self.proj_a).content.decode()
        for forbidden in ('workspace', 'platform', '"id"'):
            self.assertNotIn(forbidden, text)

    def test_stored_config_is_merged_and_pre_chat_forces_deferred_start(self):
        self.save_config(self.proj_a, {**FORM, 'launcher': {'mode': 'icon_text', 'label': 'Chat'}})
        body = self.get(self.proj_a).json()
        self.assertEqual(body['launcher']['mode'], 'icon_text')
        self.assertEqual(body['launcher']['position'], 'bottom-right')                  # default kept
        self.assertEqual(body['behavior']['start_mode'], 'on_first_message')            # coerced: a question needs no conversation yet
        self.assertEqual([f['key'] for f in body['pre_chat']['fields']],
                         ['topic', 'order_no', 'email', 'phone', 'details', 'agree', 'page'])

    def test_unknown_inactive_and_malformed_projects_are_a_uniform_404(self):
        self.proj_b.is_active = False
        self.proj_b.save()
        for key in (self.proj_b.public_key, '00000000-0000-0000-0000-000000000000', 'garbage'):
            res = self.client.get('/api/v1/widget/config/', {'project_key': str(key)})
            self.assertEqual((res.status_code, res.json()['code']), (404, 'invalid_project'))
        self.assertEqual(self.client.get('/api/v1/widget/config/').status_code, 404)

    def test_allowed_domain_policy_applies_when_an_origin_is_sent(self):
        self.proj_a.allowed_domains = 'shop-a.example.com'
        self.proj_a.save()
        self.assertEqual(self.get(self.proj_a, HTTP_ORIGIN='https://shop-a.example.com').status_code, 200)
        self.assertEqual(self.get(self.proj_a, HTTP_ORIGIN='https://evil.example.org').status_code, 403)

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=['https://dashboard.example.com'])
    def test_cors_follows_configured_project_domains(self):
        self.proj_a.allowed_domains = 'shop-a.example.com'
        self.proj_a.save()
        res = self.get(self.proj_a, HTTP_ORIGIN='https://shop-a.example.com')
        self.assertEqual(res['Access-Control-Allow-Origin'], 'https://shop-a.example.com')
        res = self.get(self.proj_a, HTTP_ORIGIN='https://unrelated.example.org')
        self.assertNotIn('Access-Control-Allow-Origin', res)


class AdminApiTests(Fixture):
    def url(self, project):
        return f'/api/v1/projects/{project.pk}/widget-config/'

    def test_workspace_admin_reads_and_writes(self):
        c = self.as_user(self.admin_a)
        res = c.put(self.url(self.proj_a), {'launcher': {'mode': 'icon_text', 'label': 'Hi'}}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['effective']['launcher']['label'], 'Hi')
        self.assertEqual(c.get(self.url(self.proj_a)).json()['config']['launcher']['mode'], 'icon_text')
        self.assertTrue(AuditEvent.objects.filter(action='widget_config_updated', target_id=str(self.proj_a.pk)).exists())

    def test_put_replaces_the_stored_document(self):
        c = self.as_user(self.admin_a)
        c.put(self.url(self.proj_a), {'launcher': {'label': 'One'}}, format='json')
        c.put(self.url(self.proj_a), {'locale': 'en'}, format='json')
        stored = c.get(self.url(self.proj_a)).json()['config']
        self.assertNotIn('launcher', stored)

    def test_invalid_document_reports_paths_and_stores_nothing(self):
        c = self.as_user(self.admin_a)
        res = c.put(self.url(self.proj_a), {'launcher': {'mode': 'banner', 'color': 'x'}}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'invalid_config')
        self.assertEqual(set(res.json()['errors']), {'launcher.mode', 'launcher.color'})
        self.assertFalse(ProjectWidgetConfig.objects.exists())

    def test_operators_and_strangers_cannot_write_or_read(self):
        for user in (self.op_a, self.admin_b):
            c = self.as_user(user)
            self.assertEqual(c.put(self.url(self.proj_a), {'locale': 'en'}, format='json').status_code,
                             403 if user == self.op_a else 404)
            self.assertIn(c.get(self.url(self.proj_a)).status_code, (403, 404))
        self.assertEqual(APIClient().get(self.url(self.proj_a)).status_code, 401)
        self.assertFalse(ProjectWidgetConfig.objects.exists())

    def test_admin_of_A_who_is_only_operator_of_B_cannot_touch_B(self):
        c = self.as_user(self.mixed)
        res = c.put(self.url(self.proj_b), {'locale': 'en'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(ProjectWidgetConfig.objects.filter(project=self.proj_b).exists())
        self.assertEqual(c.put(self.url(self.proj_a), {'locale': 'en'}, format='json').status_code, 200)


class AdminProjectListTests(Fixture):
    def test_lists_only_projects_of_workspaces_the_caller_administers(self):
        ids = lambda user: [p['id'] for p in self.as_user(user).get('/api/v1/projects/').json()]  # noqa: E731
        self.assertEqual(ids(self.admin_a), [self.proj_a.pk])
        self.assertEqual(ids(self.admin_b), [self.proj_b.pk])
        self.assertEqual(ids(self.op_a), [])                    # an operator administers nothing
        self.assertEqual(ids(self.mixed), [self.proj_a.pk])     # admin of A + operator of B: only A
        self.assertEqual(APIClient().get('/api/v1/projects/').status_code, 401)


class PreChatFlowTests(Fixture):
    def test_mode_a_no_questions_starts_immediately(self):
        token = self.guest(self.proj_a)
        res = self.start(token)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(PreChatSubmission.objects.exists())

    def test_mode_b_one_question(self):
        self.save_config(self.proj_a, {'pre_chat': {'enabled': True, 'fields': [
            {'key': 'help', 'type': 'text', 'label': 'چه کمکی می‌توانیم بکنیم؟', 'required': True}]}})
        token = self.guest(self.proj_a)
        res = self.start(token)                                                       # no answer -> refused, nothing created
        self.assertEqual((res.status_code, res.json()['code']), (400, 'pre_chat_invalid'))
        self.assertFalse(Conversation.objects.exists())
        res = self.start(token, pre_chat={'help': 'سفارش من نرسید'})
        self.assertEqual(res.status_code, 200, res.content)
        sub = PreChatSubmission.objects.get()
        self.assertEqual(sub.answers[0]['value'], 'سفارش من نرسید')

    def test_mode_c_structured_form_persisted_and_visible_to_operator(self):
        self.save_config(self.proj_a, FORM)
        token = self.guest(self.proj_a)
        res = self.start(token, pre_chat={'topic': 'billing', 'order_no': 'A-17', 'agree': True, 'page': '/orders/17'})
        self.assertEqual(res.status_code, 200, res.content)
        conv = Conversation.objects.get(pk=res.json()['id'])
        self.assertEqual({a['key']: a['value'] for a in conv.pre_chat.answers},
                         {'topic': 'billing', 'order_no': 'A-17', 'agree': True, 'page': '/orders/17'})
        ctx = self.as_user(self.op_a).get(f'/api/v1/conversations/customer/{conv.id}/customer-context/')
        self.assertEqual(ctx.status_code, 200)
        self.assertEqual([a['key'] for a in ctx.json()['pre_chat']], ['topic', 'order_no', 'agree', 'page'])
        self.assertFalse(ctx.json()['identity_verified'])
        # another store's operator cannot read it
        self.assertIn(self.as_user(self.admin_b).get(f'/api/v1/conversations/customer/{conv.id}/customer-context/').status_code,
                      (403, 404))

    def test_invalid_answers_create_nothing(self):
        self.save_config(self.proj_a, FORM)
        token = self.guest(self.proj_a)
        for answers in ({}, {'topic': 'order'}, {'topic': 'hack', 'agree': True}, {'topic': 'order', 'agree': True, 'zzz': 1}):
            res = self.start(token, pre_chat=answers)
            self.assertEqual((res.status_code, res.json()['code']), (400, 'pre_chat_invalid'), answers)
        self.assertFalse(Conversation.objects.exists())

    def test_resuming_never_asks_again_or_overwrites(self):
        self.save_config(self.proj_a, FORM)
        token = self.guest(self.proj_a)
        first = self.start(token, pre_chat={'topic': 'order', 'agree': True}).json()
        again = self.start(token)                                                     # resume without answers
        self.assertEqual((again.status_code, again.json()['id']), (200, first['id']))
        again2 = self.start(token, pre_chat={'topic': 'billing', 'agree': True})
        self.assertEqual(again2.json()['id'], first['id'])
        self.assertEqual(PreChatSubmission.objects.get().answers[0]['value'], 'order')

    def test_answers_are_ignored_when_the_project_has_no_form(self):
        token = self.guest(self.proj_a)
        res = self.start(token, pre_chat={'anything': 'x'})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(PreChatSubmission.objects.exists())

    def test_peek_never_creates_a_conversation(self):
        token = self.guest(self.proj_a)
        res = self.start(token, create=False)
        self.assertEqual((res.status_code, res.json()), (200, {'id': None}))
        self.assertFalse(Conversation.objects.exists())
        created = self.start(token).json()['id']
        peek = self.start(token, create=False).json()
        self.assertEqual(peek['id'], created)

    def test_answers_of_one_store_form_are_validated_against_that_stores_form(self):
        self.save_config(self.proj_a, FORM)                                           # B has no form
        token_b = self.guest(self.proj_b)
        res = self.start(token_b, pre_chat={'topic': 'order', 'agree': True})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(PreChatSubmission.objects.exists())

    def test_pre_chat_answers_drive_automation_conditions(self):
        from automations.conditions import ConditionContext, evaluate_condition
        from automations.events import TriggerEvent
        from automations.schema import validate_conditions
        self.save_config(self.proj_a, FORM)
        token = self.guest(self.proj_a)
        conv = Conversation.objects.get(pk=self.start(token, pre_chat={'topic': 'billing', 'agree': True}).json()['id'])
        ctx = ConditionContext(TriggerEvent('CONVERSATION_CREATED', str(self.ws_a.pk)), conv)
        cond = {'field': 'conversation.pre_chat', 'path': 'topic', 'operator': 'equals', 'value': 'billing'}
        validate_conditions(cond)
        self.assertTrue(evaluate_condition(cond, ctx))
        self.assertFalse(evaluate_condition({**cond, 'value': 'order'}, ctx))
        self.assertFalse(evaluate_condition({**cond, 'path': 'missing'}, ctx))
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'conversation.pre_chat', 'operator': 'equals', 'value': 'x'})   # path required


class IdentityPolicyTests(Fixture):
    def test_authenticated_only_blocks_guest_sessions_server_side(self):
        self.save_config(self.proj_a, {'identity': {'guest_allowed': False, 'authenticated_only': True}})
        res = self.client.post('/api/v1/widget/init/', {'project_key': str(self.proj_a.public_key)}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'identity_required'))

    def test_existing_guest_session_cannot_start_when_policy_tightens(self):
        token = self.guest(self.proj_a)
        self.save_config(self.proj_a, {'identity': {'guest_allowed': False, 'authenticated_only': True}})
        res = self.start(token)
        self.assertEqual((res.status_code, res.json()['code']), (403, 'identity_required'))
        self.assertFalse(Conversation.objects.exists())

    def test_verified_customer_still_works_when_guests_are_blocked(self):
        from integrations.testing import FakeHost
        host = FakeHost('acme')
        info = host.call(self.client, 'put', '/api/v1/integrations/tenants/shop-1/',
                         {'display_name': 'S', 'defaults': {'widget': {'identity': {'guest_allowed': False,
                                                                                    'authenticated_only': True}}}}).json()
        res = self.client.post('/api/v1/widget/init/', {'project_key': info['project_public_key']}, format='json')
        self.assertEqual(res.status_code, 403)
        a = host.assertion('customer', 'cust-1', 'shop-1')
        ex = self.client.post('/api/v1/identity/customer/', {'project_key': info['project_public_key'], 'assertion': a}, format='json')
        self.assertEqual(ex.status_code, 200, ex.content)
        start = self.start(ex.json()['session_token'])
        self.assertEqual(start.status_code, 200, start.content)
        ctx = self.as_user(self.user('s-admin', Workspace.objects.get(pk=info['workspace_id']), 'WORKSPACE_ADMIN')).get(
            f'/api/v1/conversations/customer/{start.json()["id"]}/customer-context/')
        self.assertTrue(ctx.json()['identity_verified'])


class ProvisioningWidgetDefaultsTests(Fixture):
    def provision(self, host, tenant='shop-1', **body):
        return host.call(self.client, 'put', f'/api/v1/integrations/tenants/{tenant}/', {'display_name': 'S', **body})

    def test_provisioned_tenants_defer_conversation_start_by_default(self):
        from integrations.testing import FakeHost
        host = FakeHost('acme')
        info = self.provision(host).json()
        body = self.client.get('/api/v1/widget/config/', {'project_key': info['project_public_key']}).json()
        self.assertEqual(body['behavior']['start_mode'], 'on_first_message')

    def test_seeded_widget_document_is_applied_once_and_never_overwritten(self):
        from integrations.testing import FakeHost
        host = FakeHost('acme')
        seed = {'launcher': {'mode': 'icon_text', 'label': 'Help'}, **FORM}
        info = self.provision(host, defaults={'widget': seed}).json()
        project = Project.objects.get(public_key=info['project_public_key'])
        self.assertEqual(project.widget_config.config['launcher']['label'], 'Help')
        # an admin edits it by hand, the host re-sends different defaults
        project.widget_config.config = validate_config({'launcher': {'label': 'Manual'}})
        project.widget_config.save()
        self.provision(host, defaults={'widget': {'launcher': {'label': 'Overwrite attempt'}}})
        project.widget_config.refresh_from_db()
        self.assertEqual(project.widget_config.config['launcher']['label'], 'Manual')

    def test_invalid_seed_is_rejected_and_creates_nothing(self):
        from integrations.testing import FakeHost
        host = FakeHost('acme')
        res = self.provision(host, defaults={'widget': {'launcher': {'mode': 'banner'}}})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Project.objects.filter(workspace__integration_mapping__isnull=False).exists())
