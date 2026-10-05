# Extra SYNTHETIC data for the matrix, run through `manage.py shell` after seed_staging_data. Idempotent.
# Writes ids/credentials (staging-only, random) to $STG_DIR/seed-extra.json (chmod 600) for the test drivers.
import json, os, secrets
from django.utils import timezone
from accounts.models import User
from conversations.models import Conversation, Message
from platforms.models import Platform, PlatformMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from workspaces.models import Workspace, WorkspaceMembership

out_path = os.path.join(os.environ['STG_DIR'], 'seed-extra.json')
data = json.load(open(out_path)) if os.path.exists(out_path) else {}
pw = lambda: secrets.token_urlsafe(18)
D = 'staging.rastichat.local'

def user(local, name, **kw):
    u, created = User.objects.get_or_create(email=f'{local}@{D}', defaults={'display_name': f'STAGING — {name}', **kw})
    if created or local not in data.get('users', {}):
        p = pw(); u.set_password(p); u.is_active = kw.get('is_active', True); u.save()
        data.setdefault('users', {})[local] = {'email': u.email, 'password': p, 'id': str(u.pk)}
    return u

platform = Platform.objects.get(name='STAGING — Rastisi Platform')
wsA = Workspace.objects.get(name='STAGING — Workspace A')
projA = Project.objects.get(name='STAGING — Sample Website')
projA.allowed_domains = 'embed-allowed.example.test'
projA.save()

wsB, _ = Workspace.objects.get_or_create(name='STAGING — Workspace B', defaults={'platform': platform})
projB, _ = Project.objects.get_or_create(name='STAGING — Store B', defaults={'workspace': wsB})
adminB = user('admin-b', 'مدیر فروشگاه ب', is_staff=True)
WorkspaceMembership.objects.get_or_create(user=adminB, workspace=wsB, defaults={'role': 'WORKSPACE_OWNER'})

agentA = user('agent-a1', 'اپراتور آزمایشی', is_staff=True)
WorkspaceMembership.objects.get_or_create(user=agentA, workspace=wsA, defaults={'role': 'WORKSPACE_OPERATOR'})
inactive = user('inactive-a', 'کاربر غیرفعال', is_staff=True, is_active=False)
WorkspaceMembership.objects.get_or_create(user=inactive, workspace=wsA, defaults={'role': 'WORKSPACE_OPERATOR'})
victim = user('victim-a', 'اپراتور برای ابطال', is_staff=True)
WorkspaceMembership.objects.get_or_create(user=victim, workspace=wsA, defaults={'role': 'WORKSPACE_OPERATOR'})
super_ = user('platform-super', 'سوپریوزر پلتفرم', is_staff=True)
if not super_.is_superuser:
    super_.is_superuser = True; super_.save()
support = user('platform-agent', 'پشتیبان پلتفرم', is_staff=True)
PlatformMembership.objects.get_or_create(user=support, platform=platform, defaults={'role': 'PLATFORM_SUPPORT_AGENT'})

def conv(ws, proj, ext, texts):
    v, _ = Visitor.objects.get_or_create(project=proj, external_id=ext)
    s = VisitorSession.objects.filter(visitor=v).first() or VisitorSession.objects.create(visitor=v)
    c = Conversation.objects.filter(visitor=v, type=Conversation.Type.CUSTOMER).first() or Conversation.objects.create(
        workspace=ws, project=proj, visitor=v, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN)
    if not c.messages.exists():
        for n, t in enumerate(texts):
            Message.objects.create(conversation=c, sender_type=Message.SenderType.VISITOR, content=t, client_message_id=f'seed-{ext}-{n}')
    return {'conversation': str(c.id), 'session_token': str(s.token), 'visitor': str(v.id)}

data['A'] = {'workspace': str(wsA.id), 'project_key': str(projA.public_key), **conv(wsA, projA, 'stg-vis-a1', ['سلام، سفارش من کی می‌رسد؟', 'کد پیگیری: ۱۲۳۴۵۶‌‌ — لطفاً راهنمایی کنید 🙏'])}
data['B'] = {'workspace': str(wsB.id), 'project_key': str(projB.public_key), **conv(wsB, projB, 'stg-vis-b1', ['پیام مشتری فروشگاه ب'])}
data['support_conversation_a'] = None
sc = Conversation.objects.filter(workspace=wsA, type=Conversation.Type.PLATFORM_SUPPORT).first() or Conversation.objects.create(
    workspace=wsA, type=Conversation.Type.PLATFORM_SUPPORT, status=Conversation.Status.WAITING_FOR_PLATFORM, subject='پشتیبانی آزمایشی')
data['support_conversation_a'] = str(sc.id)
with open(out_path, 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.chmod(out_path, 0o600)
print('seed-extra written')
