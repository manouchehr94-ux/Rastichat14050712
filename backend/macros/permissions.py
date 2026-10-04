"""Macro authorization. Every check resolves against the macro's OWN
workspace_id (never "any workspace the user happens to belong to") — the
same discipline common.permissions.user_has_workspace_role already
enforces, which is what makes a user who is Admin in Workspace A and only
an Operator in Workspace B unable to manage Workspace B's macros with
Workspace A privileges (spec section 12, test 42).
"""
from rest_framework.exceptions import PermissionDenied

from common.permissions import user_has_workspace_role

from .models import Macro


def _is_workspace_admin(user, workspace_id):
    return user_has_workspace_role(user, workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN'])


def _is_team_supervisor(user, team_id):
    if not team_id:
        return False
    from teams.models import TeamMembership
    return TeamMembership.objects.filter(team_id=team_id, user=user, role='SUPERVISOR', is_active=True).exists()


def _is_team_member(user, team_id):
    if not team_id:
        return False
    from teams.models import TeamMembership
    return TeamMembership.objects.filter(team_id=team_id, user=user, is_active=True).exists()


def can_view_macro(user, macro):
    if not user_has_workspace_role(user, macro.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN', 'WORKSPACE_OPERATOR']):
        return False
    if _is_workspace_admin(user, macro.workspace_id):
        return True
    if macro.visibility == Macro.Visibility.WORKSPACE:
        return True
    if macro.visibility == Macro.Visibility.TEAM:
        return _is_team_member(user, macro.team_id)
    if macro.visibility == Macro.Visibility.PRIVATE:
        return macro.owner_id == user.id
    return False


def can_execute_macro(user, macro):
    return macro.is_active and can_view_macro(user, macro)


def can_create_macro(user, workspace, visibility, *, team=None):
    if _is_workspace_admin(user, workspace.id):
        return True
    is_operator = user_has_workspace_role(user, workspace.id, ['WORKSPACE_OPERATOR'])
    if not is_operator:
        return False
    if visibility == Macro.Visibility.PRIVATE:
        # "create private macros only if explicitly enabled" — enabled by
        # default for any workspace operator; see docs/knowledge_base_and_macros.md
        # for the deliberate simplification (no per-workspace policy model
        # in this phase).
        return True
    if visibility == Macro.Visibility.TEAM:
        return team is not None and _is_team_supervisor(user, team.id)
    return False  # WORKSPACE-visibility macros are admin-only to create


def can_manage_macro(user, macro):
    """Edit/delete/activate/duplicate. Owner/Admin can manage everything in
    their workspace; an Operator may only manage a PRIVATE macro they own.
    """
    if _is_workspace_admin(user, macro.workspace_id):
        return True
    if macro.visibility == Macro.Visibility.PRIVATE and macro.owner_id == user.id:
        return True
    return False


def require_can_view_macro(user, macro):
    if not can_view_macro(user, macro):
        raise PermissionDenied('Not authorized to view this macro')


def require_can_execute_macro(user, macro):
    if not can_execute_macro(user, macro):
        raise PermissionDenied('Not authorized to execute this macro')


def require_can_manage_macro(user, macro):
    if not can_manage_macro(user, macro):
        raise PermissionDenied('Not authorized to manage this macro')
