from django.contrib import admin
from .models import Tag, ConversationTag, Note, CustomerProfile, CustomerOrder

admin.site.register(Tag)
admin.site.register(ConversationTag)
admin.site.register(Note)
admin.site.register(CustomerProfile)
admin.site.register(CustomerOrder)
