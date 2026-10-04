from rest_framework import serializers
from .models import Project

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'name', 'public_key', 'allowed_domains', 'is_active']
        read_only_fields = ['public_key']
