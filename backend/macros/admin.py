from django.contrib import admin

from .models import Macro, MacroActionExecution, MacroExecution

admin.site.register(Macro)
admin.site.register(MacroExecution)
admin.site.register(MacroActionExecution)
