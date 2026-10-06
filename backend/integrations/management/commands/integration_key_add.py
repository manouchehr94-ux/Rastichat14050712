from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError

from integrations import audit
from integrations.keys import load_public_key, new_kid
from integrations.models import Integration, IntegrationKey


class Command(BaseCommand):
    help = ('Register a host signing key (Ed25519 PUBLIC key, PEM). Rotation = add the new key, switch the host over, '
            'then revoke the old one with integration_key_revoke. Prints the key id (kid) the host puts in its JWS header.')

    def add_arguments(self, parser):
        parser.add_argument('--integration', required=True, help='integration slug')
        parser.add_argument('--public-key-file', required=True)
        parser.add_argument('--scopes', help='comma separated subset of the integration scopes (default: all of them)')
        parser.add_argument('--expires-at', help='ISO-8601 UTC timestamp')

    def handle(self, *args, **opts):
        try:
            integration = Integration.objects.get(slug=opts['integration'])
        except Integration.DoesNotExist:
            raise CommandError('Integration not found.')
        with open(opts['public_key_file'], encoding='utf-8') as fh:
            pem = fh.read()
        try:
            load_public_key(pem)
        except ValueError as exc:
            raise CommandError(str(exc))
        scopes = ([s.strip() for s in opts['scopes'].split(',') if s.strip()] if opts['scopes']
                  else list(integration.scopes))
        if set(scopes) - set(integration.scopes):
            raise CommandError('Key scopes must be a subset of the integration scopes.')
        expires_at = None
        if opts['expires_at']:
            expires_at = datetime.fromisoformat(opts['expires_at'].replace('Z', '+00:00'))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=dt_timezone.utc)
        key = IntegrationKey(integration=integration, kid=new_kid(), public_key_pem=pem.strip() + '\n',
                             scopes=scopes, expires_at=expires_at)
        key.full_clean()
        key.save()
        audit.record('key_added', integration=integration, key=key, target_type='integration_key', target_id=key.pk)
        self.stdout.write(self.style.SUCCESS(f'kid={key.kid}'))
