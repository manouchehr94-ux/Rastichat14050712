from django.urls import path

from .views import TenantView, WhoAmIView

urlpatterns = [
    path('me/', WhoAmIView.as_view(), name='integration-me'),
    path('tenants/<str:external_tenant_id>/', TenantView.as_view(), name='integration-tenant'),
]
