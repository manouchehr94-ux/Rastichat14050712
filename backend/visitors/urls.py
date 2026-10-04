from django.urls import path
from .views import InitVisitorView
urlpatterns = [path('', InitVisitorView.as_view(), name='widget-init')]
