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
