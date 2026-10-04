import os

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import User
from automations.models import AutomationRule
from workspaces.models import Workspace


class SeedStagingDataTests(TestCase):
    def tearDown(self):
        if os.path.exists('test-staging-credentials.txt'):
            os.remove('test-staging-credentials.txt')

    def test_refuses_without_yes_flag(self):
        with self.assertRaises(CommandError):
            call_command('seed_staging_data')

    @override_settings(ENVIRONMENT='production')
    def test_refuses_in_production_even_with_yes(self):
        with self.assertRaises(CommandError):
            call_command('seed_staging_data', '--yes')

    def test_creates_clearly_marked_non_production_accounts(self):
        call_command('seed_staging_data', '--yes', '--output=test-staging-credentials.txt')
        self.assertTrue(Workspace.objects.filter(name='STAGING — Workspace A').exists())
        owner = User.objects.get(email='owner@staging.rastichat.local')
        self.assertTrue(owner.display_name.startswith('STAGING —'))
        self.assertTrue(os.path.exists('test-staging-credentials.txt'))
        mode = oct(os.stat('test-staging-credentials.txt').st_mode)[-3:]
        self.assertEqual(mode, '600')
        rule = AutomationRule.objects.get(name='STAGING — تعیین اولویت بالا')
        self.assertFalse(rule.is_active)

    def test_is_idempotent(self):
        call_command('seed_staging_data', '--yes', '--output=test-staging-credentials.txt')
        call_command('seed_staging_data', '--yes', '--output=test-staging-credentials.txt')
        self.assertEqual(Workspace.objects.filter(name='STAGING — Workspace A').count(), 1)
        self.assertEqual(User.objects.filter(email='owner@staging.rastichat.local').count(), 1)

    def test_passwords_are_not_the_shared_dev_default(self):
        call_command('seed_staging_data', '--yes', '--output=test-staging-credentials.txt')
        owner = User.objects.get(email='owner@staging.rastichat.local')
        self.assertFalse(owner.check_password('pass1234'))
