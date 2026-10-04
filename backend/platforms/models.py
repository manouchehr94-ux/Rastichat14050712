from django.db import models
from django.conf import settings

class Platform(models.Model):
    name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class PlatformMembership(models.Model):
    class Roles(models.TextChoices):
        PLATFORM_OWNER = 'PLATFORM_OWNER', 'Platform Owner'
        PLATFORM_ADMIN = 'PLATFORM_ADMIN', 'Platform Admin'
        PLATFORM_SUPPORT_AGENT = 'PLATFORM_SUPPORT_AGENT', 'Platform Support Agent'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='platform_memberships')
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=50, choices=Roles.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'platform')
