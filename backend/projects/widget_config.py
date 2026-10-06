"""Versioned widget configuration (launcher, start behaviour, pre-chat form, identity policy).

Stored per project as a *partial* document (`ProjectWidgetConfig.config`); the effective configuration is the stored
document deep-merged over `DEFAULTS` (lists such as pre-chat `fields` are replaced, not merged). Everything is
validated server-side against a strict schema — unknown keys are rejected, sizes are bounded, no free-form regular
expressions (no ReDoS surface), no HTML (text only).

Pre-chat is pure configuration: no questions, one question, or a structured form need no code change. Answers are stored
as structured data with a snapshot of the question labels (see `conversations.PreChatSubmission`), never as columns.
"""
import copy
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

SCHEMA_VERSION = 1

LAUNCHER_MODES = ('icon', 'icon_text')
POSITIONS = ('bottom-right', 'bottom-left')
ICONS = ('chat', 'help', 'headset', 'mail', 'sparkle')
START_MODES = ('on_load', 'on_open', 'on_first_message')
FIELD_TYPES = ('text', 'textarea', 'email', 'phone', 'select', 'radio', 'checkbox', 'consent', 'hidden')
CHOICE_TYPES = ('select', 'radio')
LOCALES = ('fa', 'en')
DIRECTIONS = ('rtl', 'ltr')
MAX_FIELDS = 12
MAX_CHOICES = 20
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
PHONE_RE = re.compile(r'^\+?[0-9()\-\s]{5,20}$')
PATH_RE = re.compile(r'^/[A-Za-z0-9/_\-.*]{0,198}$')

DEFAULTS = {
    'version': SCHEMA_VERSION,
    'launcher': {
        'enabled': True, 'mode': 'icon', 'position': 'bottom-right', 'offset': {'x': 20, 'y': 20},
        'label': '', 'tooltip': '', 'icon': 'chat', 'color': '#BC5A38', 'greeting': '', 'auto_open': False,
        'mobile': {'fullscreen': True},
    },
    'visibility': {'hide_on_paths': [], 'show_on_paths': []},
    # on_load: the conversation is created when the page loads (the original widget behaviour, kept for projects that
    # have not opted in). on_open / on_first_message avoid creating empty conversations in the inbox.
    'behavior': {'start_mode': 'on_load'},
    'pre_chat': {'enabled': False, 'title': '', 'submit_label': '', 'fields': []},
    'identity': {'guest_allowed': True, 'authenticated_only': False},
    'locale': 'fa',
    'direction': 'rtl',
    'capabilities': {'attachments': True, 'voice': True, 'emoji': True, 'rating': True},
}


class ConfigError(Exception):
    """Validation failure: `.errors` maps a dotted path to a message."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


class _Collector:
    def __init__(self):
        self.errors = {}

    def add(self, path, message):
        self.errors.setdefault(path, message)

    def raise_if_any(self):
        if self.errors:
            raise ConfigError(self.errors)


def _text(c, path, value, max_len, *, allow_blank=True):
    if not isinstance(value, str):
        c.add(path, 'Must be text.')
        return ''
    value = value.strip()
    if len(value) > max_len:
        c.add(path, f'At most {max_len} characters.')
    if not value and not allow_blank:
        c.add(path, 'Required.')
    if re.search(r'[<>\u0000-\u0008\u000b\u000c\u000e-\u001f]', value):
        c.add(path, 'Markup and control characters are not allowed.')
    return value


def _bool(c, path, value):
    if not isinstance(value, bool):
        c.add(path, 'Must be true or false.')
        return False
    return value


def _enum(c, path, value, options):
    if value not in options:
        c.add(path, f'Must be one of: {", ".join(options)}.')
        return options[0]
    return value


def _only_keys(c, path, data, allowed):
    if not isinstance(data, dict):
        c.add(path, 'Must be an object.')
        return False
    for key in data:
        if key not in allowed:
            c.add(f'{path}.{key}' if path else key, 'Unknown field.')
    return True


def _paths(c, path, value):
    if not isinstance(value, list) or len(value) > 20:
        c.add(path, 'Must be a list of at most 20 path patterns.')
        return []
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not PATH_RE.match(item):
            c.add(f'{path}[{i}]', 'Must be a path such as /checkout or /blog/* (letters, digits, / _ - . *).')
        else:
            out.append(item)
    return out


def _validate_field(c, path, field, seen_keys):
    allowed = {'key', 'type', 'label', 'placeholder', 'required', 'order', 'choices', 'max_length', 'enabled'}
    if not _only_keys(c, path, field, allowed):
        return None
    out = {}
    key = field.get('key')
    if not isinstance(key, str) or not KEY_RE.match(key):
        c.add(f'{path}.key', 'Lower-case letters, digits and _, starting with a letter (max 40).')
        key = None
    elif key in seen_keys:
        c.add(f'{path}.key', 'Duplicate key.')
    else:
        seen_keys.add(key)
    out['key'] = key
    ftype = _enum(c, f'{path}.type', field.get('type'), FIELD_TYPES)
    out['type'] = ftype
    out['label'] = _text(c, f'{path}.label', field.get('label', ''), 120, allow_blank=(ftype == 'hidden'))
    out['placeholder'] = _text(c, f'{path}.placeholder', field.get('placeholder', ''), 120)
    out['required'] = _bool(c, f'{path}.required', field.get('required', False))
    out['enabled'] = _bool(c, f'{path}.enabled', field.get('enabled', True))
    order = field.get('order', 0)
    if isinstance(order, bool) or not isinstance(order, int) or not (0 <= order <= 1000):
        c.add(f'{path}.order', 'Must be an integer between 0 and 1000.')
        order = 0
    out['order'] = order
    max_length = field.get('max_length')
    if max_length is not None:
        if isinstance(max_length, bool) or not isinstance(max_length, int) or not (1 <= max_length <= 2000):
            c.add(f'{path}.max_length', 'Must be an integer between 1 and 2000.')
            max_length = None
    out['max_length'] = max_length
    choices = field.get('choices', [])
    clean_choices = []
    if ftype in CHOICE_TYPES:
        if not isinstance(choices, list) or not (1 <= len(choices) <= MAX_CHOICES):
            c.add(f'{path}.choices', f'Between 1 and {MAX_CHOICES} choices are required.')
        else:
            values = set()
            for i, choice in enumerate(choices):
                cpath = f'{path}.choices[{i}]'
                if not _only_keys(c, cpath, choice, {'value', 'label'}):
                    continue
                value = _text(c, f'{cpath}.value', choice.get('value', ''), 80, allow_blank=False)
                label = _text(c, f'{cpath}.label', choice.get('label', ''), 120, allow_blank=False)
                if value in values:
                    c.add(f'{cpath}.value', 'Duplicate value.')
                values.add(value)
                clean_choices.append({'value': value, 'label': label})
    elif choices:
        c.add(f'{path}.choices', 'Only select and radio fields have choices.')
    out['choices'] = clean_choices
    if ftype == 'consent' and not out['label']:
        c.add(f'{path}.label', 'A consent checkbox needs the consent text as its label.')
    return out


def validate_config(data):
    """Validate a (possibly partial) config document; returns the normalised partial document. Raises `ConfigError`."""
    c = _Collector()
    if not isinstance(data, dict):
        raise ConfigError({'': 'Must be an object.'})
    if not _only_keys(c, '', data, set(DEFAULTS)):
        c.raise_if_any()
    out = {}
    if 'version' in data and data['version'] != SCHEMA_VERSION:
        c.add('version', f'Unsupported version; this server speaks version {SCHEMA_VERSION}.')
    out['version'] = SCHEMA_VERSION

    if 'launcher' in data:
        src = data['launcher']
        launcher = {}
        if _only_keys(c, 'launcher', src, set(DEFAULTS['launcher'])):
            if 'enabled' in src:
                launcher['enabled'] = _bool(c, 'launcher.enabled', src['enabled'])
            if 'mode' in src:
                launcher['mode'] = _enum(c, 'launcher.mode', src['mode'], LAUNCHER_MODES)
            if 'position' in src:
                launcher['position'] = _enum(c, 'launcher.position', src['position'], POSITIONS)
            if 'icon' in src:
                launcher['icon'] = _enum(c, 'launcher.icon', src['icon'], ICONS)
            for key, limit in (('label', 40), ('tooltip', 120), ('greeting', 200)):
                if key in src:
                    launcher[key] = _text(c, f'launcher.{key}', src[key], limit)
            if 'color' in src:
                if isinstance(src['color'], str) and COLOR_RE.match(src['color']):
                    launcher['color'] = src['color'].upper()
                else:
                    c.add('launcher.color', 'Must be a #RRGGBB colour.')
            if 'auto_open' in src:
                launcher['auto_open'] = _bool(c, 'launcher.auto_open', src['auto_open'])
            if 'offset' in src:
                offset = src['offset']
                if _only_keys(c, 'launcher.offset', offset, {'x', 'y'}):
                    clean = {}
                    for axis in ('x', 'y'):
                        value = offset.get(axis, DEFAULTS['launcher']['offset'][axis])
                        if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 200):
                            c.add(f'launcher.offset.{axis}', 'Must be an integer between 0 and 200.')
                            value = DEFAULTS['launcher']['offset'][axis]
                        clean[axis] = value
                    launcher['offset'] = clean
            if 'mobile' in src:
                mobile = src['mobile']
                if _only_keys(c, 'launcher.mobile', mobile, {'fullscreen'}):
                    launcher['mobile'] = {'fullscreen': _bool(c, 'launcher.mobile.fullscreen', mobile.get('fullscreen', True))}
        out['launcher'] = launcher

    if 'visibility' in data:
        src = data['visibility']
        vis = {}
        if _only_keys(c, 'visibility', src, {'hide_on_paths', 'show_on_paths'}):
            for key in ('hide_on_paths', 'show_on_paths'):
                if key in src:
                    vis[key] = _paths(c, f'visibility.{key}', src[key])
        out['visibility'] = vis

    if 'behavior' in data:
        src = data['behavior']
        behavior = {}
        if _only_keys(c, 'behavior', src, {'start_mode'}) and 'start_mode' in src:
            behavior['start_mode'] = _enum(c, 'behavior.start_mode', src['start_mode'], START_MODES)
        out['behavior'] = behavior

    if 'pre_chat' in data:
        src = data['pre_chat']
        pre = {}
        if _only_keys(c, 'pre_chat', src, set(DEFAULTS['pre_chat'])):
            if 'enabled' in src:
                pre['enabled'] = _bool(c, 'pre_chat.enabled', src['enabled'])
            for key, limit in (('title', 120), ('submit_label', 40)):
                if key in src:
                    pre[key] = _text(c, f'pre_chat.{key}', src[key], limit)
            if 'fields' in src:
                fields = src['fields']
                if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
                    c.add('pre_chat.fields', f'Must be a list of at most {MAX_FIELDS} fields.')
                else:
                    seen = set()
                    clean = [_validate_field(c, f'pre_chat.fields[{i}]', f, seen) for i, f in enumerate(fields)]
                    pre['fields'] = [f for f in clean if f is not None]
            if pre.get('enabled') and not [f for f in pre.get('fields', []) if f.get('enabled', True)
                                           and f.get('type') != 'hidden']:
                c.add('pre_chat.fields', 'Pre-chat is enabled but has no visible question.')
        out['pre_chat'] = pre

    if 'identity' in data:
        src = data['identity']
        identity = {}
        if _only_keys(c, 'identity', src, {'guest_allowed', 'authenticated_only'}):
            for key in ('guest_allowed', 'authenticated_only'):
                if key in src:
                    identity[key] = _bool(c, f'identity.{key}', src[key])
            if identity.get('authenticated_only') and identity.get('guest_allowed'):
                c.add('identity', 'authenticated_only and guest_allowed cannot both be true.')
        out['identity'] = identity

    if 'locale' in data:
        out['locale'] = _enum(c, 'locale', data['locale'], LOCALES)
    if 'direction' in data:
        out['direction'] = _enum(c, 'direction', data['direction'], DIRECTIONS)

    if 'capabilities' in data:
        src = data['capabilities']
        caps = {}
        if _only_keys(c, 'capabilities', src, set(DEFAULTS['capabilities'])):
            for key in src:
                if key in DEFAULTS['capabilities']:
                    caps[key] = _bool(c, f'capabilities.{key}', src[key])
        out['capabilities'] = caps
    c.raise_if_any()
    return out


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_config(project):
    """The effective, fully populated configuration for a project (defaults + stored overrides)."""
    stored = getattr(getattr(project, 'widget_config', None), 'config', None) or {}
    config = _merge(DEFAULTS, stored)
    config['pre_chat']['fields'] = sorted(
        [f for f in config['pre_chat']['fields'] if f.get('enabled', True)], key=lambda f: (f.get('order', 0), f['key']))
    if config['pre_chat']['enabled'] and config['behavior']['start_mode'] == 'on_load':
        # a question can only be asked before the conversation exists
        config['behavior']['start_mode'] = 'on_first_message'
    if config['identity']['authenticated_only']:
        config['identity']['guest_allowed'] = False
    return config


def guests_allowed(project):
    """Server-side enforcement point of `identity.guest_allowed` / `authenticated_only` (the widget UI is not the gate)."""
    return bool(resolve_config(project)['identity']['guest_allowed'])


def public_config(project):
    """What the widget receives (`GET /api/v1/widget/config/`): configuration + the project's public branding."""
    config = resolve_config(project)
    config['project'] = {'key': str(project.public_key), 'name': project.name, 'logo_url': project.logo_url,
                         'subtitle': project.subtitle}
    return config


# ----------------------------------------------------------------------------------------- answers
def validate_answers(config, answers):
    """Validate a visitor's pre-chat `answers` ({field key: value}) against the effective `config`.

    Returns the snapshot list stored with the conversation — `[{key, label, type, value, source}]` — where the label
    is copied so the operator still sees the question if the form is edited later. `hidden` fields carry
    host-page-supplied context and are flagged `source: 'client'` (never trusted as identity). Raises `ConfigError`.
    """
    c = _Collector()
    answers = answers if isinstance(answers, dict) else None
    if answers is None:
        raise ConfigError({'pre_chat': 'Must be an object of answers.'})
    fields = {f['key']: f for f in config['pre_chat']['fields']}
    for key in answers:
        if key not in fields:
            c.add(key, 'Unknown question.')
    snapshot = []
    for key, field in fields.items():
        ftype = field['type']
        raw = answers.get(key)
        empty = raw is None or raw == '' or raw is False
        if ftype in ('consent', 'checkbox'):
            value = raw is True
            if field['required'] and not value:
                c.add(key, 'Required.')
        elif empty:
            if field['required'] and ftype != 'hidden':
                c.add(key, 'Required.')
            continue
        else:
            if not isinstance(raw, str):
                c.add(key, 'Must be text.')
                continue
            value = raw.strip()
            limit = field.get('max_length') or (2000 if ftype == 'textarea' else 255 if ftype == 'hidden' else 500)
            if len(value) > limit:
                c.add(key, f'At most {limit} characters.')
            if re.search(r'[\u0000-\u0008\u000b\u000c\u000e-\u001f]', value):
                c.add(key, 'Control characters are not allowed.')
            if ftype == 'email':
                try:
                    validate_email(value)
                except DjangoValidationError:
                    c.add(key, 'Enter a valid email address.')
            elif ftype == 'phone' and not PHONE_RE.match(value):
                c.add(key, 'Enter a valid phone number.')
            elif ftype in CHOICE_TYPES and value not in {ch['value'] for ch in field['choices']}:
                c.add(key, 'Choose one of the listed options.')
        snapshot.append({'key': key, 'label': field['label'], 'type': ftype, 'value': value,
                         'source': 'client' if ftype == 'hidden' else 'visitor'})
    c.raise_if_any()
    return snapshot
