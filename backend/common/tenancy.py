from rest_framework.exceptions import PermissionDenied, ValidationError


def resolve_operator_workspace(user, requested_workspace_id=None):
    """Resolve and validate the workspace a workspace-operator request targets.

    Never guesses across tenants: a workspace is only implied when the user
    has exactly one membership. Otherwise `requested_workspace_id` must be
    supplied and must match one of the user's own memberships.
    """
    memberships = list(user.workspace_memberships.select_related('workspace').all())
    if not memberships:
        raise PermissionDenied('No workspace membership')
    if requested_workspace_id:
        for membership in memberships:
            if str(membership.workspace_id) == str(requested_workspace_id):
                return membership.workspace
        raise PermissionDenied('Not a member of the requested workspace')
    if len(memberships) > 1:
        raise ValidationError(
            {'workspace_id': 'Must specify a workspace_id when a member of multiple workspaces'}
        )
    return memberships[0].workspace


ADMIN_ROLES = ('WORKSPACE_OWNER', 'WORKSPACE_ADMIN')


def admin_workspace_ids(user):
    """Ids of the workspaces in which `user` is Owner/Admin — never 'admin
    somewhere'. Used to scope querysets for admin-only resources.
    """
    return user.workspace_memberships.filter(role__in=ADMIN_ROLES).values_list('workspace_id', flat=True)


def resolve_admin_workspace(user, requested_workspace_id=None):
    """Resolve the workspace an admin-only request targets, validating the
    caller is Owner/Admin of that EXACT workspace.

    Mirrors `resolve_operator_workspace`, but only admin memberships count
    (an operator role in another workspace never qualifies). A workspace is
    implied only when the user administers exactly one; an admin of several
    must say which one, and it must be one they administer.
    """
    memberships = list(
        user.workspace_memberships.select_related('workspace').filter(role__in=ADMIN_ROLES)
    )
    if not memberships:
        raise PermissionDenied('Not an admin of any workspace')
    if requested_workspace_id not in (None, ''):
        for membership in memberships:
            if str(membership.workspace_id) == str(requested_workspace_id):
                return membership.workspace
        raise PermissionDenied('Not an admin of the requested workspace')
    if len(memberships) > 1:
        raise ValidationError(
            {'workspace_id': 'Must specify a workspace_id when an admin of multiple workspaces'}
        )
    return memberships[0].workspace
