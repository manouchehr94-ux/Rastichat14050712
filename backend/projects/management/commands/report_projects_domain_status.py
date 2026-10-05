from django.core.management.base import BaseCommand

from projects.domains import lenient_entries, parse_allowed_domains
from projects.models import Project
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help = (
        'List ACTIVE projects whose allowed_domains is empty or contains invalid entries — run this before turning on '
        'WIDGET_REQUIRE_ALLOWED_DOMAINS. Prints ids and names only (no keys, no customer data). Read-only.'
    )

    def handle(self, *args, **options):
        empty, invalid = [], []
        for p in Project.objects.filter(is_active=True).select_related('workspace'):
            if not lenient_entries(p.allowed_domains):
                empty.append(p)
                continue
            try:
                parse_allowed_domains(p.allowed_domains)
            except ValidationError:
                invalid.append(p)
        self.stdout.write(f'{len(empty)} active project(s) with NO usable allowed_domains:')
        for p in empty:
            self.stdout.write(f'  project #{p.id} "{p.name}" (workspace #{p.workspace_id})')
        self.stdout.write(f'{len(invalid)} active project(s) with some INVALID entries (ignored at runtime):')
        for p in invalid:
            self.stdout.write(f'  project #{p.id} "{p.name}" (workspace #{p.workspace_id})')
