from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

class IsPlatformOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.platform_memberships.filter(role='PLATFORM_OWNER').exists()

class IsPlatformAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.platform_memberships.filter(role__in=['PLATFORM_OWNER', 'PLATFORM_ADMIN']).exists()

class IsPlatformSupportAgent(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.platform_memberships.filter(role__in=['PLATFORM_OWNER', 'PLATFORM_ADMIN', 'PLATFORM_SUPPORT_AGENT']).exists()

class IsWorkspaceOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.workspace_memberships.filter(role='WORKSPACE_OWNER').exists()

class IsWorkspaceAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.workspace_memberships.filter(role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']).exists()

class IsWorkspaceOperator(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.workspace_memberships.filter(role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN', 'WORKSPACE_OPERATOR']).exists()

def user_has_workspace_role(user, workspace, roles):
    """Does `user` hold one of `roles` in this EXACT workspace — never 'in
    any workspace'. `workspace` may be a Workspace instance or an id.
    """
    if not workspace:
        return False
    return user.workspace_memberships.filter(workspace=workspace, role__in=roles).exists()


def user_can_supervise_workspace(user, workspace):
    """Owner/Admin of this exact workspace, or an active team SUPERVISOR
    within a team belonging to this exact workspace. A SUPERVISOR role held
    in a *different* workspace never counts — this is the fix for the
    audited cross-workspace supervisor-summary leak.
    """
    if user_has_workspace_role(user, workspace, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']):
        return True
    from teams.models import TeamMembership
    return TeamMembership.objects.filter(
        user=user, team__workspace=workspace, role='SUPERVISOR', is_active=True,
    ).exists()


def require_workspace_admin(user, workspace):
    """Raise a 403 unless `user` is Owner/Admin of this EXACT workspace.
    For use in custom create()/action methods, which resolve their target
    workspace themselves and so can't rely on has_object_permission (no
    existing object to check against yet).
    """
    if not user_has_workspace_role(user, workspace, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']):
        raise PermissionDenied('Only an admin of this workspace can perform this action')


class IsWorkspaceAdminOfObject(permissions.BasePermission):
    """Object-level Owner/Admin check scoped to the SPECIFIC workspace the
    object belongs to — never 'admin of any workspace'. has_permission is
    intentionally just an authentication gate: the real enforcement is
    has_object_permission, which DRF's GenericAPIView.get_object() invokes
    automatically (via check_object_permissions) once the target object is
    resolved, for both standard actions (update/destroy) and any custom
    @action that calls self.get_object(). Actions with no existing object
    (create) must call require_workspace_admin() explicitly instead — this
    class can't help them since there is nothing to check yet.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        workspace_id = getattr(obj, 'workspace_id', None)
        if not workspace_id:
            return False
        return user_has_workspace_role(request.user, workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN'])
