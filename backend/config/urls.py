from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path('api/v1/', include('config.api_urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
