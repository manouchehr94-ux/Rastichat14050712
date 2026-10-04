from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Shared pagination for the team-operations resource APIs.

    Existing endpoints from earlier stages intentionally return plain arrays
    (frontends already depend on that shape) and are left untouched — this
    is only applied to the new team/queue/SLA/collaboration/notification
    list endpoints added in this stage.
    """
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100
