# Server-side state changes used by the revocation / lifecycle tests (run through `manage.py shell`; staging DB only).
import os
from django.utils import timezone
from accounts.models import User
from projects.models import Project
from visitors.models import VisitorSession
from workspaces.models import Workspace, WorkspaceMembership

action, arg = os.environ['ADMIN_ACTION'], os.environ.get('ADMIN_ARG', '')
WS_A = 'STAGING — Workspace A'

if action == 'deactivate-user':
    User.objects.filter(email=arg).update(is_active=False)
elif action == 'activate-user':
    User.objects.filter(email=arg).update(is_active=True)
elif action == 'remove-membership':
    WorkspaceMembership.objects.filter(user__email=arg, workspace__name=WS_A).delete()
elif action == 'restore-membership':
    u = User.objects.get(email=arg)
    WorkspaceMembership.objects.get_or_create(user=u, workspace=Workspace.objects.get(name=WS_A), defaults={'role': 'WORKSPACE_OPERATOR'})
elif action == 'revoke-visitor-session':
    VisitorSession.objects.filter(token=arg).update(revoked_at=timezone.now())
elif action == 'expire-visitor-session':
    VisitorSession.objects.filter(token=arg).update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
elif action == 'restore-visitor-session':
    VisitorSession.objects.filter(token=arg).update(revoked_at=None, expires_at=timezone.now() + timezone.timedelta(days=30))
elif action == 'deactivate-project':
    Project.objects.filter(public_key=arg).update(is_active=False)
elif action == 'activate-project':
    Project.objects.filter(public_key=arg).update(is_active=True)
elif action == 'deactivate-workspace':
    Workspace.objects.filter(name=arg or WS_A).update(is_active=False)
elif action == 'activate-workspace':
    Workspace.objects.filter(name=arg or WS_A).update(is_active=True)
else:
    raise SystemExit(f'unknown action {action}')
print('ok', action)
