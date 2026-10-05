from rest_framework import serializers
from .models import Project
from .domains import normalize_allowed_domains_text, validate_allowed_domains

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'name', 'public_key', 'allowed_domains', 'is_active']
        read_only_fields = ['public_key']

    def validate_allowed_domains(self, value):
        validate_allowed_domains(value)  # raises a field error naming the bad entry
        return normalize_allowed_domains_text(value)
