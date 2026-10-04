from rest_framework import serializers
from .models import Tag, Note, CustomerOrder


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'color', 'created_at']
        read_only_fields = ['id', 'created_at']


class NoteSerializer(serializers.ModelSerializer):
    created_by_email = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = ['id', 'conversation', 'body', 'created_by_email', 'created_at', 'updated_at']
        read_only_fields = ['id', 'conversation', 'created_by_email', 'created_at', 'updated_at']

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by else None


class CustomerOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerOrder
        fields = ['id', 'external_order_id', 'product_name', 'product_image', 'price', 'status', 'ordered_at']
