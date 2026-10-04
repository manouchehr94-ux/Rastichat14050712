from django.urls import path
from .views import InitVisitorView, RevokeVisitorSessionView, RotateVisitorSessionView

urlpatterns = [path('', InitVisitorView.as_view(), name='widget-init')]
session_urlpatterns = [
    path('revoke/', RevokeVisitorSessionView.as_view(), name='widget-session-revoke'),
    path('rotate/', RotateVisitorSessionView.as_view(), name='widget-session-rotate'),
]
