class WorkspaceScopedQuerysetMixin:
    """Shared workspace-tenancy filter for the team-operations resource viewsets.

    Requires the model to have a `workspace` FK (directly or via
    `workspace_field`). Never trusts a client-supplied workspace filter for
    isolation — only the requesting user's own memberships decide what's
    visible.
    """
    workspace_field = 'workspace'

    def get_queryset(self):
        lookup = {f'{self.workspace_field}__memberships__user': self.request.user}
        return self.queryset.model.objects.filter(**lookup).distinct()
