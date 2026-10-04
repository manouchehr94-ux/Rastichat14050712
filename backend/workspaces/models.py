from django.db import models
from django.conf import settings
from platforms.models import Platform

class Workspace(models.Model):
    name = models.CharField(max_length=255)
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name='workspaces')
    external_id = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class WorkspaceMembership(models.Model):
    class Roles(models.TextChoices):
        WORKSPACE_OWNER = 'WORKSPACE_OWNER', 'Workspace Owner'
        WORKSPACE_ADMIN = 'WORKSPACE_ADMIN', 'Workspace Admin'
        WORKSPACE_OPERATOR = 'WORKSPACE_OPERATOR', 'Workspace Operator'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace_memberships')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=50, choices=Roles.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'workspace')
