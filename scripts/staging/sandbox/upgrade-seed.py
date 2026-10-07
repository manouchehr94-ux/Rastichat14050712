"""Synthetic EXISTING state, written with the PREVIOUS release's models (origin/main), run before the upgrade migrations.
Deliberately awkward: two ACTIVE platform-support threads in one workspace (legal on main; the new partial unique index must not
choke on them), closed/resolved rows, visitor sessions, notes, tags, audit rows, a project with allowed domains."""
import json, os
from django.utils import timezone
from accounts.models import User
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from conversations.models import Conversation, Message

plat, _ = Platform.objects.get_or_create(name='Upgrade Platform')
owner = User.objects.create_user(email='up-owner@example.test', password='x', display_name='Up Owner')
PlatformMembership.objects.create(user=owner, platform=plat, role='PLATFORM_OWNER')
counts = {}
for w in range(3):
    ws = Workspace.objects.create(name=f'up-ws-{w}', platform=plat)
    WorkspaceMembership.objects.create(user=owner, workspace=ws, role='WORKSPACE_OWNER')
    proj = Project.objects.create(name=f'up-proj-{w}', workspace=ws)
    proj.allowed_domains = f'up{w}.example.test'; proj.save()
    for v in range(4):
        vis = Visitor.objects.create(project=proj, external_id=f'legacy-{w}-{v}', name=f'Legacy {w}.{v}')
        for c, status in enumerate(('OPEN', 'CLOSED')):
            conv = Conversation.objects.create(workspace=ws, project=proj, visitor=vis, type='CUSTOMER', status=status, subject=f'legacy {w}.{v}.{c}')
            for m in range(3):
                Message.objects.create(conversation=conv, sender_type='VISITOR', sender_visitor=vis, content=f'legacy message {m}', client_message_id=f'{w}-{v}-{c}-{m}')
    for k in range(2):   # two ACTIVE support threads in the same workspace + one closed
        sc = Conversation.objects.create(workspace=ws, type='PLATFORM_SUPPORT', status='WAITING_FOR_PLATFORM', subject=f'legacy support {w}.{k}')
        Message.objects.create(conversation=sc, sender_type='USER', sender=owner, content='legacy support message', client_message_id=f's-{w}-{k}')
    Conversation.objects.create(workspace=ws, type='PLATFORM_SUPPORT', status='CLOSED', subject=f'legacy closed support {w}')
from audit.models import AuditEvent
for i in range(5):
    AuditEvent.objects.create(actor=owner, action='legacy.seeded', target_type='x', target_id=str(i), metadata={})
counts = {m.__name__: m.objects.count() for m in (Workspace, Project, Visitor, Conversation, Message, AuditEvent, User)}
json.dump(counts, open(os.environ['COUNTS_OUT'], 'w'))
print('seeded existing state', counts)
