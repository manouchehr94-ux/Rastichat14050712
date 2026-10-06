from django.urls import path

from . import views_identity as vi
from .views import TenantView, WhoAmIView

urlpatterns = [
    path('me/', WhoAmIView.as_view(), name='integration-me'),
    path('tenants/<str:external_tenant_id>/', TenantView.as_view(), name='integration-tenant'),
    path('tenants/<str:external_tenant_id>/members/<str:external_user_id>/', vi.TenantMemberView.as_view(),
         name='integration-tenant-member'),
    path('platform/members/<str:external_user_id>/', vi.PlatformMemberView.as_view(), name='integration-platform-member'),
    path('users/<str:external_user_id>/disable/', vi.StaffIdentityStateView.as_view(action='disable'),
         name='integration-user-disable'),
    path('users/<str:external_user_id>/enable/', vi.StaffIdentityStateView.as_view(action='enable'),
         name='integration-user-enable'),
    path('tenants/<str:external_tenant_id>/customers/<str:external_user_id>/disable/',
         vi.CustomerIdentityStateView.as_view(action='disable'), name='integration-customer-disable'),
    path('tenants/<str:external_tenant_id>/customers/<str:external_user_id>/enable/',
         vi.CustomerIdentityStateView.as_view(action='enable'), name='integration-customer-enable'),
]

identity_urlpatterns = [
    path('customer/', vi.CustomerExchangeView.as_view(), name='identity-customer'),
    path('staff/', vi.StaffExchangeView.as_view(), name='identity-staff'),
]
