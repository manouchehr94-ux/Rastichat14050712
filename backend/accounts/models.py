from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)

class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    # Operator-facing identity shown to visitors in the widget (e.g. "مشاور ارشد").
    # All optional — the widget must fall back gracefully when unset, never invent a name.
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)
    title = models.CharField(max_length=255, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class OperatorPresence(models.Model):
    class Status(models.TextChoices):
        ONLINE = 'ONLINE', 'Online'
        AWAY = 'AWAY', 'Away'
        BUSY = 'BUSY', 'Busy'
        DO_NOT_DISTURB = 'DO_NOT_DISTURB', 'Do Not Disturb'
        OFFLINE = 'OFFLINE', 'Offline'

    # Statuses the operator chose deliberately — activity pings never
    # silently override them the way a stale ONLINE/AWAY claim gets decayed.
    EXPLICIT_STATUSES = (Status.OFFLINE, Status.BUSY, Status.DO_NOT_DISTURB)

    ONLINE_STALE_AFTER = timedelta(minutes=2)
    AWAY_STALE_AFTER = timedelta(minutes=10)

    DEFAULT_MAX_CAPACITY = 10

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='presence')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OFFLINE)
    last_active_at = models.DateTimeField(auto_now=True)
    # Configurable per-agent capacity ceiling, used by automatic assignment
    # (LEAST_ACTIVE strategy and the at-capacity exclusion). Live count of
    # currently-active conversations is computed on demand, never stored,
    # so it can never drift.
    max_capacity = models.PositiveIntegerField(default=DEFAULT_MAX_CAPACITY)

    def touch(self, status=None):
        """Record activity (WS connect, message send, mark-read) and optionally set an explicit status."""
        if status:
            self.status = status
        else:
            self.save(update_fields=['last_active_at'])
            return
        self.save(update_fields=['status', 'last_active_at'])

    def effective_status(self):
        """A deliberately-chosen status (OFFLINE/BUSY/DND) always wins; otherwise
        staleness of last activity overrides a stale ONLINE/AWAY claim.
        """
        if self.status in self.EXPLICIT_STATUSES:
            return self.status
        age = timezone.now() - self.last_active_at
        if age > self.AWAY_STALE_AFTER:
            return self.Status.OFFLINE
        if age > self.ONLINE_STALE_AFTER:
            return self.Status.AWAY
        return self.status

    def active_conversation_count(self):
        """Live count of conversations currently assigned to this agent that
        aren't in a terminal state — never stored/denormalized.
        """
        from conversations.models import Conversation
        return Conversation.objects.filter(
            assigned_to=self.user,
        ).exclude(status__in=[Conversation.Status.CLOSED, Conversation.Status.RESOLVED]).count()

    def is_at_capacity(self, capacity_override=None):
        capacity = capacity_override if capacity_override is not None else self.max_capacity
        return self.active_conversation_count() >= capacity
