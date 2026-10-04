"""Shared object-graph helpers for the knowledge_base test suite."""
from accounts.models import User
from conversations.models import Conversation
from platforms.models import Platform
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from workspaces.models import Workspace, WorkspaceMembership


class KBTestMixin:
    _counter = 0

    def _seq(self):
        KBTestMixin._counter += 1
        return KBTestMixin._counter

    def make_workspace(self, name=None):
        name = name or f'WS{self._seq()}'
        platform = Platform.objects.create(name=f'{name}-platform')
        return Workspace.objects.create(name=name, platform=platform)

    def make_project(self, workspace, name='Store'):
        return Project.objects.create(workspace=workspace, name=name)

    def make_visitor(self, project, external_id=None):
        return Visitor.objects.create(project=project, external_id=external_id or f'visitor-{self._seq()}')

    def make_visitor_session(self, visitor):
        return VisitorSession.objects.create(visitor=visitor)

    def make_conversation(self, workspace, project, visitor, **kwargs):
        defaults = dict(type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN)
        defaults.update(kwargs)
        return Conversation.objects.create(workspace=workspace, project=project, visitor=visitor, **defaults)

    def make_user(self, email=None):
        email = email or f'user{self._seq()}@test.com'
        return User.objects.create_user(email=email, password='pass1234')

    def make_admin(self, workspace, email=None, role='WORKSPACE_OWNER'):
        user = self.make_user(email)
        WorkspaceMembership.objects.create(user=user, workspace=workspace, role=role)
        return user

    def make_operator(self, workspace, email=None):
        return self.make_admin(workspace, email, role='WORKSPACE_OPERATOR')

    def login(self, client, user, password='pass1234'):
        res = client.post('/api/v1/auth/login/', {'email': user.email, 'password': password}, format='json')
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
