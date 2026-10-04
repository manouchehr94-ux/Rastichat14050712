"""Controlled, opt-in staging demo data — deliberately separate from the
repo-root `seed_data.py` used by local dev/E2E, which creates accounts with
a single well-known password ('pass1234') that must never exist on any
internet-reachable environment.

Every account created here:
  - lives under the @staging.rastichat.local email domain and carries a
    "STAGING —" display-name prefix, so it can never be mistaken for a real
    customer/operator account in the admin or database;
  - gets its own randomly generated password (or one supplied via
    STAGING_SEED_PASSWORD, e.g. for a CI-reproducible smoke-test run) —
    never a fixed default;
  - is created idempotently (get_or_create), so re-running this command is
    safe and never resets a password an operator has since changed by hand.

Refuses to run at all when ENVIRONMENT=production — there is no legitimate
reason for demo accounts to exist in a real production deployment, and this
command does not accept an override flag for that.
"""
import os
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from automations.models import AutomationRule
from customer_context.models import Tag
from knowledge_base.models import KnowledgeBaseCategory
from macros.models import Macro
from platforms.models import Platform, PlatformMembership
from projects.models import Project
from queues.models import Queue
from sla.models import SLAPolicy
from teams.models import Team, TeamMembership
from workspaces.models import Workspace, WorkspaceMembership

EMAIL_DOMAIN = 'staging.rastichat.local'


def _generate_password():
    return secrets.token_urlsafe(18)


class Command(BaseCommand):
    help = (
        'Create clearly-marked, non-production staging demo data '
        '(workspace, owner/supervisor/operators, sample KB, inactive macros). '
        'Requires --yes. Refuses to run when ENVIRONMENT=production.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help='Required explicit opt-in — the command does nothing without this flag.',
        )
        parser.add_argument(
            '--output', default='',
            help='Path to write the generated credentials to (chmod 600). '
                 'Defaults to STAGING_SEED_CREDENTIALS_PATH or ./staging-credentials.txt.',
        )

    def handle(self, *args, **options):
        if settings.ENVIRONMENT == 'production':
            raise CommandError(
                'seed_staging_data refuses to run when ENVIRONMENT=production. '
                'Demo/staging accounts must never exist in a real production deployment.'
            )
        if not options['yes']:
            raise CommandError('Refusing to seed staging data without --yes (explicit opt-in required).')

        credentials = []

        def make_user(email_local, display_name, is_staff=True):
            email = f'{email_local}@{EMAIL_DOMAIN}'
            user, created = User.objects.get_or_create(
                email=email, defaults={'is_staff': is_staff, 'display_name': f'STAGING — {display_name}'},
            )
            if created:
                password = os.environ.get('STAGING_SEED_PASSWORD') or _generate_password()
                user.set_password(password)
                user.save()
                credentials.append((email, password))
            else:
                credentials.append((email, '(already existed — password unchanged)'))
            return user

        platform, _ = Platform.objects.get_or_create(name='STAGING — Rastisi Platform')

        owner = make_user('owner', 'مالک فضای‌کار')
        ws, _ = Workspace.objects.get_or_create(name='STAGING — Workspace A', defaults={'platform': platform})
        WorkspaceMembership.objects.get_or_create(user=owner, workspace=ws, defaults={'role': 'WORKSPACE_OWNER'})

        supervisor = make_user('supervisor', 'سرپرست')
        WorkspaceMembership.objects.get_or_create(user=supervisor, workspace=ws, defaults={'role': 'WORKSPACE_ADMIN'})

        operator1 = make_user('operator1', 'اپراتور یک')
        WorkspaceMembership.objects.get_or_create(user=operator1, workspace=ws, defaults={'role': 'WORKSPACE_OPERATOR'})
        operator2 = make_user('operator2', 'اپراتور دو')
        WorkspaceMembership.objects.get_or_create(user=operator2, workspace=ws, defaults={'role': 'WORKSPACE_OPERATOR'})

        team, _ = Team.objects.get_or_create(
            workspace=ws, name='STAGING — پشتیبانی', defaults={'description': 'تیم پشتیبانی نمایشی'},
        )
        TeamMembership.objects.get_or_create(team=team, user=operator1, defaults={'role': 'MEMBER', 'is_active': True})
        TeamMembership.objects.get_or_create(team=team, user=operator2, defaults={'role': 'SUPERVISOR', 'is_active': True})
        Queue.objects.get_or_create(
            workspace=ws, name='STAGING — صف پشتیبانی', defaults={'team': team, 'assignment_strategy': Queue.Strategy.MANUAL},
        )
        SLAPolicy.objects.get_or_create(
            workspace=ws, name='STAGING — SLA', defaults={
                'first_response_target_minutes': 15, 'resolution_target_minutes': 240, 'is_active': True,
            },
        )

        project, _ = Project.objects.get_or_create(name='STAGING — Sample Website', defaults={'workspace': ws})

        for i, name in enumerate(['سفارش‌ها', 'پرداخت', 'ارسال']):
            KnowledgeBaseCategory.objects.get_or_create(
                workspace=ws, slug=f'staging-{i}-{name}', defaults={'name': name, 'is_active': False, 'sort_order': i},
            )

        demo_tag, _ = Tag.objects.get_or_create(workspace=ws, name='STAGING — نمونه', defaults={'color': '#7f8c8d'})
        Macro.objects.get_or_create(
            workspace=ws, name='STAGING — پاسخ نمونه', defaults={
                'actions': [
                    {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، این یک پاسخ نمونه محیط آزمایشی است.'}},
                    {'type': 'ADD_TAG', 'params': {'tag_id': str(demo_tag.id)}},
                ],
                'visibility': Macro.Visibility.WORKSPACE, 'is_active': False,
                'description': 'ماکروی نمایشی محیط استیجینگ — قبل از استفاده بررسی و فعال شود.',
            },
        )

        # Inactive by default, same convention as the KB category/macro
        # above — an admin (the staging smoke suite itself, scenario 14)
        # must deliberately activate it before it fires. Exists so the
        # smoke suite can verify real automation execution without needing
        # to drive the full condition-builder UI from a blind script.
        AutomationRule.objects.get_or_create(
            workspace=ws, name='STAGING — تعیین اولویت بالا', defaults={
                'trigger_type': AutomationRule.Trigger.CONVERSATION_CREATED,
                'conditions': {}, 'actions': [{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
                'is_active': False,
            },
        )

        output_path = options['output'] or os.environ.get('STAGING_SEED_CREDENTIALS_PATH') or 'staging-credentials.txt'
        lines = [f'{email}: {password}' for email, password in credentials]
        lines.append(f'Project public_key: {project.public_key}')
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(output_path, 0o600)

        self.stdout.write(self.style.SUCCESS(
            f'Staging demo data ready. Credentials written once to {output_path} (chmod 600) — '
            f'this is the only place they are stored; they are not re-printed on subsequent runs for '
            f'accounts that already existed.'
        ))
        for line in lines:
            self.stdout.write(f'  {line}')
