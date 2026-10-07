import os

from django.core.management.base import BaseCommand, CommandError

from integrations.keys import generate_keypair


class Command(BaseCommand):
    help = ('DEVELOPMENT/REFERENCE helper: generate an Ed25519 keypair into --out-dir (private key mode 0600). '
            'A real host generates its own keypair and sends only the public key; never generate production host '
            'keys on the RastiChat server.')

    def add_arguments(self, parser):
        parser.add_argument('--out-dir', required=True)
        parser.add_argument('--name', default='integration')

    def handle(self, *args, **opts):
        out = opts['out_dir']
        os.makedirs(out, exist_ok=True)
        private_path = os.path.join(out, f'{opts["name"]}.private.pem')
        public_path = os.path.join(out, f'{opts["name"]}.public.pem')
        if os.path.exists(private_path) or os.path.exists(public_path):
            raise CommandError('Refusing to overwrite an existing key file.')
        private_pem, public_pem = generate_keypair()
        fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as fh:
            fh.write(private_pem)
        with open(public_path, 'w', encoding='utf-8') as fh:
            fh.write(public_pem)
        self.stdout.write(f'private: {private_path}\npublic:  {public_path}')
