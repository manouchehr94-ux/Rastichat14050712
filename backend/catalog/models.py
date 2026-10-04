import uuid
from django.db import models
from workspaces.models import Workspace


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='products')
    external_id = models.CharField(max_length=255, blank=True)
    brand = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=0)
    old_price = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    currency = models.CharField(max_length=8, default='IRT')
    rating = models.DecimalField(max_digits=2, decimal_places=1, default=5)
    reviews_count = models.PositiveIntegerField(default=0)
    image = models.URLField(blank=True)
    product_url = models.URLField(blank=True)
    is_available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name
