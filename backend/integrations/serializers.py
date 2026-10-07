import re

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from projects.domains import normalize_entry

_SECRETISH_KEY = re.compile(r'(secret|passw|token|credential|private|api[-_]?key|authorization)', re.I)
MAX_METADATA_BYTES = 2048


class BrandingDefaultsSerializer(serializers.Serializer):
    logo_url = serializers.URLField(required=False, allow_blank=True, max_length=200)
    subtitle = serializers.CharField(required=False, allow_blank=True, max_length=255)


class DefaultsSerializer(serializers.Serializer):
    """Values applied ONLY when the tenant is first created (seed-only). Later changes belong to RastiChat admins;
    an integration re-sending them does not overwrite manual configuration."""
    branding = BrandingDefaultsSerializer(required=False)
    # a widget configuration document (launcher, pre-chat, identity policy, ...) — see projects/widget_config.py
    widget = serializers.DictField(required=False)

    def validate_widget(self, value):
        from projects.widget_config import ConfigError, validate_config
        try:
            return validate_config(value)
        except ConfigError as exc:
            raise serializers.ValidationError(exc.errors)


class _StrictMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({k: 'Unknown field.' for k in unknown})
        return super().to_internal_value(data)


class StrictDefaultsSerializer(_StrictMixin, DefaultsSerializer):
    pass


class TenantUpsertSerializer(_StrictMixin, serializers.Serializer):
    display_name = serializers.CharField(required=False, max_length=255, trim_whitespace=True)
    verified_domains = serializers.ListField(child=serializers.CharField(max_length=255), required=False, max_length=50)
    status = serializers.ChoiceField(choices=['active', 'suspended'], required=False)
    defaults = StrictDefaultsSerializer(required=False)
    metadata = serializers.DictField(required=False)

    def validate_display_name(self, value):
        if not value.strip():
            raise serializers.ValidationError('This field may not be blank.')
        return value

    def validate_verified_domains(self, value):
        out = []
        for entry in value:
            if entry.strip().startswith('*'):
                raise serializers.ValidationError(f'"{entry}": wildcards are not accepted; send exact verified hostnames.')
            try:
                norm = normalize_entry(entry)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages)
            if norm not in out:
                out.append(norm)
        return out

    def validate_metadata(self, value):
        import json
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64:
                raise serializers.ValidationError('Metadata keys must be strings of at most 64 characters.')
            if _SECRETISH_KEY.search(key):
                raise serializers.ValidationError(f'"{key}": metadata must never carry credentials or secrets.')
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                raise serializers.ValidationError(f'"{key}": metadata values must be scalars.')
        if len(json.dumps(value)) > MAX_METADATA_BYTES:
            raise serializers.ValidationError(f'Metadata is limited to {MAX_METADATA_BYTES} bytes.')
        return value
