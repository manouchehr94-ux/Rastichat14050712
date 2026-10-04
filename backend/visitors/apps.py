from django.apps import AppConfig

class VisitorsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'visitors'

    def ready(self):
        from . import checks  # noqa: F401  (registers the deploy security check)
