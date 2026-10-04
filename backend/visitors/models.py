import uuid
from django.db import models
from projects.models import Project

class Visitor(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='visitors')
    external_id = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    mobile = models.CharField(max_length=20, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Visitor {self.id}"

class VisitorSession(models.Model):
    visitor = models.ForeignKey(Visitor, on_delete=models.CASCADE, related_name='sessions')
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return str(self.token)
