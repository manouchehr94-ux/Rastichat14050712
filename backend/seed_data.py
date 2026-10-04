import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from accounts.models import User
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from catalog.models import Product
from teams.models import Team, TeamMembership
from queues.models import Queue
from sla.models import SLAPolicy
from customer_context.models import Tag
from knowledge_base.models import KnowledgeBaseCategory
from macros.models import Macro

print("Seeding database...")

# 1. Create Platform
platform, _ = Platform.objects.get_or_create(name='RastiChat Platform')

# 2. Create Platform Owner
po, created = User.objects.get_or_create(email='platform@rasti.com', defaults={'is_staff': True, 'is_superuser': True})
if created: po.set_password('pass1234'); po.save()
PlatformMembership.objects.get_or_create(user=po, platform=platform, defaults={'role': 'PLATFORM_OWNER'})

# 3. Create Workspace
ws, _ = Workspace.objects.get_or_create(name='Sample Workspace', defaults={'platform': platform})

# 4. Create Workspace Admin
wa, created = User.objects.get_or_create(email='admin@ws.com', defaults={'is_staff': True})
if created: wa.set_password('pass1234'); wa.save()
WorkspaceMembership.objects.get_or_create(user=wa, workspace=ws, defaults={'role': 'WORKSPACE_ADMIN'})

# 5. Create Workspace Operator
wo, created = User.objects.get_or_create(email='operator@ws.com', defaults={'is_staff': True})
if created: wo.set_password('pass1234'); wo.save()
WorkspaceMembership.objects.get_or_create(user=wo, workspace=ws, defaults={'role': 'WORKSPACE_OPERATOR'})

# 5b. Second workspace operator, used by E2E claim-race / transfer scenarios
wo2, created = User.objects.get_or_create(email='operator2@ws.com', defaults={'is_staff': True, 'display_name': 'همکار دو'})
if created: wo2.set_password('pass1234'); wo2.save()
WorkspaceMembership.objects.get_or_create(user=wo2, workspace=ws, defaults={'role': 'WORKSPACE_OPERATOR'})

# 5c. A team both operators belong to, plus a queue and a short-fuse SLA
# policy so E2E can deterministically exercise approaching/breach states
# without waiting on real wall-clock time.
sales_team, _ = Team.objects.get_or_create(workspace=ws, name='فروش', defaults={'description': 'تیم فروش و پشتیبانی مشتریان'})
TeamMembership.objects.get_or_create(team=sales_team, user=wo, defaults={'role': 'MEMBER', 'is_active': True})
TeamMembership.objects.get_or_create(team=sales_team, user=wo2, defaults={'role': 'SUPERVISOR', 'is_active': True})

sales_queue, _ = Queue.objects.get_or_create(
    workspace=ws, name='صف فروش', defaults={'team': sales_team, 'assignment_strategy': Queue.Strategy.MANUAL},
)

# A second team, used as the transfer destination in E2E.
tech_team, _ = Team.objects.get_or_create(workspace=ws, name='فنی', defaults={'description': 'پشتیبانی فنی'})
TeamMembership.objects.get_or_create(team=tech_team, user=wo2, defaults={'role': 'MEMBER', 'is_active': True})

SLAPolicy.objects.get_or_create(
    workspace=ws, name='Fast E2E SLA',
    defaults={'first_response_target_minutes': 1, 'resolution_target_minutes': 2, 'is_active': True},
)

# 6. Create Platform Support Agent
psa, created = User.objects.get_or_create(email='support@platform.com', defaults={'is_staff': True})
if created: psa.set_password('pass1234'); psa.save()
PlatformMembership.objects.get_or_create(user=psa, platform=platform, defaults={'role': 'PLATFORM_SUPPORT_AGENT'})

# 7. Create Project
proj, _ = Project.objects.get_or_create(name='Sample Website', defaults={'workspace': ws})

# 8. Sample product catalog (used by the operator dashboard's "share product" picker)
sample_products = [
    {'name': 'شمع معطر وانیل و کهره', 'brand': 'آرُم هوم', 'price': 890000, 'old_price': 1120000, 'rating': 5, 'reviews_count': 128},
    {'name': 'گلدان سرامیکی مینیمال', 'brand': 'آرُم هوم', 'price': 1450000, 'old_price': None, 'rating': 5, 'reviews_count': 86},
    {'name': 'ست ماگ سرامیکی کرمی', 'brand': 'آرُم هوم', 'price': 650000, 'old_price': None, 'rating': 5, 'reviews_count': 64},
]
for p in sample_products:
    Product.objects.get_or_create(workspace=ws, name=p['name'], defaults=p)

# 9. Knowledge Base starter categories — inactive drafts, admin review
# required before they're usable. Never published/active by default (spec
# section 15): an admin must deliberately activate each one.
kb_starter_categories = [
    'سفارش‌ها', 'پرداخت', 'ارسال', 'مرجوعی', 'حساب کاربری', 'محصولات', 'مشکلات فنی',
]
for i, name in enumerate(kb_starter_categories):
    KnowledgeBaseCategory.objects.get_or_create(
        workspace=ws, slug=f'starter-{i}-{name}', defaults={'name': name, 'is_active': False, 'sort_order': i},
    )

# 10. Macro starter templates — inactive, WORKSPACE-visibility drafts that
# reference only resources this same seed already created (sales_team,
# tech_team, real tags). Never active by default — an admin must review and
# activate each one (spec section 15).
refund_tag, _ = Tag.objects.get_or_create(workspace=ws, name='مرجوعی', defaults={'color': '#e67e22'})
damaged_tag, _ = Tag.objects.get_or_create(workspace=ws, name='آسیب‌دیده', defaults={'color': '#c0392b'})
payment_tag, _ = Tag.objects.get_or_create(workspace=ws, name='مشکل پرداخت', defaults={'color': '#8e44ad'})

macro_templates = [
    {
        'name': 'درخواست مرجوعی', 'category': 'مرجوعی',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، درخواست مرجوعی شما ثبت شد و به‌زودی بررسی می‌شود.'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(refund_tag.id)}},
            {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
            {'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(sales_team.id), 'reason': 'درخواست مرجوعی'}},
            {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'نیاز به بررسی و تأیید مرجوعی توسط تیم فروش.'}},
            {'type': 'SET_STATUS', 'params': {'status': 'WAITING_FOR_WORKSPACE'}},
        ],
    },
    {
        'name': 'پیگیری سفارش', 'category': 'سفارش‌ها',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، سفارش شما (شماره {order_number}) در حال بررسی است.'}},
        ],
    },
    {
        'name': 'محصول آسیب‌دیده', 'category': 'مرجوعی',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، بابت این مشکل عذرخواهی می‌کنیم. لطفاً تصویری از {product_name} آسیب‌دیده ارسال کنید.'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(damaged_tag.id)}},
            {'type': 'ASSIGN_TO_TEAM', 'params': {'team_id': str(sales_team.id)}},
            {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
        ],
    },
    {
        'name': 'مشکل پرداخت', 'category': 'پرداخت',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، مشکل پرداخت شما را بررسی می‌کنیم.'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(payment_tag.id)}},
            {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
        ],
    },
    {
        'name': 'انتقال به تیم فنی', 'category': 'مشکلات فنی',
        'actions': [
            {'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(tech_team.id), 'reason': 'مشکل فنی'}},
            {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'به تیم فنی ارجاع داده شد.'}},
        ],
    },
    {
        'name': 'درخواست تصویر', 'category': 'مرجوعی',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، لطفاً یک تصویر واضح از موضوع ارسال کنید تا سریع‌تر بررسی شود.'}},
        ],
    },
    {
        'name': 'پایان موفق گفتگو', 'category': 'عمومی',
        'actions': [
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، خوشحالیم که توانستیم کمک کنیم. روز خوبی داشته باشید!'}},
            {'type': 'REQUEST_RATING', 'params': {}},
            {'type': 'CLOSE_CONVERSATION', 'params': {}},
        ],
    },
]
for template in macro_templates:
    Macro.objects.get_or_create(
        workspace=ws, name=template['name'],
        defaults={
            'category': template['category'], 'actions': template['actions'],
            'visibility': Macro.Visibility.WORKSPACE, 'is_active': False,
            'description': 'قالب آماده — قبل از استفاده توسط مدیر فضای‌کار بررسی و فعال شود.',
        },
    )

print("\n=== Seed Data Created Successfully ===")
print("Operator Login   : operator@ws.com / pass1234")
print("Operator 2 Login : operator2@ws.com / pass1234")
print("Admin Login      : admin@ws.com / pass1234")
print("Platform Support : support@platform.com / pass1234")
print(f"Project Public Key: {proj.public_key}")
print(f"Sales Team ID: {sales_team.id}")
print(f"Sales Queue ID: {sales_queue.id}")
print("======================================")
