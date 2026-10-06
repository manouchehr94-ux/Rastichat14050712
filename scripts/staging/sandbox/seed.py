import json, os
from accounts.models import User
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
PW = os.environ['STAGING_SEED_PASSWORD']
def user(email, name, **kw):
    u, _ = User.objects.get_or_create(email=email, defaults={'display_name': name, **kw})
    u.set_password(PW); u.is_active = kw.get('is_active', True); u.save(); return u
plat, _ = Platform.objects.get_or_create(name='STG Platform')
wsa, _ = Workspace.objects.get_or_create(name='ws-a', defaults={'platform': plat})
wsb, _ = Workspace.objects.get_or_create(name='ws-b', defaults={'platform': plat})
oa = user('owner-a@example.test', 'owner-a', is_staff=True)
a1 = user('agent-a1@example.test', 'agent-a1', is_staff=True)
a2 = user('agent-a2@example.test', 'agent-a2', is_staff=True)
b1 = user('agent-b1@example.test', 'agent-b1', is_staff=True)
sup = user('platform-super@example.test', 'platform-super', is_staff=True, is_superuser=True)
ina = user('inactive@example.test', 'inactive', is_staff=True, is_active=False)
for u, ws, role in [(oa, wsa, 'WORKSPACE_OWNER'), (a1, wsa, 'WORKSPACE_OPERATOR'), (a2, wsa, 'WORKSPACE_OPERATOR'), (b1, wsb, 'WORKSPACE_OPERATOR'), (ina, wsa, 'WORKSPACE_OPERATOR')]:
    WorkspaceMembership.objects.get_or_create(user=u, workspace=ws, defaults={'role': role})
PlatformMembership.objects.get_or_create(user=sup, platform=plat, defaults={'role': 'PLATFORM_OWNER'})
pa, _ = Project.objects.get_or_create(name='proj-a', defaults={'workspace': wsa})
pa.allowed_domains = 'embed-allowed.example.test'; pa.save()
pb, _ = Project.objects.get_or_create(name='proj-b', defaults={'workspace': wsb})
json.dump({'projectA': str(pa.public_key), 'projectB': str(pb.public_key)}, open(os.environ['SEED_OUT'], 'w'))
print('seeded', pa.public_key)
