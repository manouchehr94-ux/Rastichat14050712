"""Run with the NEW release against the upgraded database: the existing rows survived and the new code works on top of them."""
import json, os
from django.db import connection
from conversations.models import Conversation, Message
from integrations.models import Integration, IntegrationTenantMapping
before = json.load(open(os.environ['COUNTS_OUT']))
from accounts.models import User
from audit.models import AuditEvent
from projects.models import Project
from visitors.models import Visitor
from workspaces.models import Workspace
after = {m.__name__: m.objects.count() for m in (Workspace, Project, Visitor, Conversation, Message, AuditEvent, User)}
assert after == before or os.environ.get("ALLOW_EXTRA"), (before, after)
# legacy rows got the neutral defaults, nothing was reinterpreted
assert not Conversation.objects.exclude(subject_key='').exists(), 'legacy conversations must have an empty subject_key'
assert Conversation.objects.filter(type='PLATFORM_SUPPORT', status='WAITING_FOR_PLATFORM').count() == 6   # both active threads of every workspace kept
with connection.cursor() as cur:
    cur.execute("select indexname from pg_indexes where indexname='uniq_active_support_thread'")
    assert cur.fetchone(), 'partial unique index must exist'
assert not Integration.objects.exists() and not IntegrationTenantMapping.objects.exists()
# the new code works on legacy data: a platform thread with an explicit subject_key alongside the legacy ones
from conversations import support_service as svc
from accounts.models import User as U
ws = Workspace.objects.get(name='up-ws-0'); owner = U.objects.get(email='up-owner@example.test')
conv, created, _ = svc.start_thread(ws, owner, 'tenant', subject='after upgrade', subject_key='general', message='hello after the upgrade', client_message_id='post-upgrade-1')
assert created
conv2, created2, _ = svc.start_thread(ws, owner, 'tenant', subject='after upgrade', subject_key='general', message='again', client_message_id='post-upgrade-2')
assert conv2.id == conv.id and not created2
print('UPGRADE VERIFIED', after)
