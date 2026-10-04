from rest_framework import serializers
from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    discount_percent = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'external_id', 'brand', 'name', 'price', 'old_price', 'currency', 'discount_percent',
            'rating', 'reviews_count', 'image', 'product_url', 'is_available', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'discount_percent', 'created_at']

    def get_discount_percent(self, obj):
        if not obj.old_price or obj.old_price <= 0 or obj.old_price <= obj.price:
            return 0
        return round((1 - (obj.price / obj.old_price)) * 100)
