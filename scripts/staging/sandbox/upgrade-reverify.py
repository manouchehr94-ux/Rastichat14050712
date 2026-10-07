"""After reversing and re-applying every new migration: all legacy rows are still there (the one thread created by the verify step too)."""
import json, os
from conversations.models import Conversation, Message
from workspaces.models import Workspace
from visitors.models import Visitor
before = json.load(open(os.environ['COUNTS_OUT']))
assert Workspace.objects.count() == before['Workspace'] and Visitor.objects.count() == before['Visitor']
assert Conversation.objects.count() == before['Conversation'] + 1 and Message.objects.count() == before['Message'] + 2
assert Conversation.objects.filter(subject__startswith='legacy ').count() == before['Conversation']
print('REVERIFIED after reverse + re-apply')
