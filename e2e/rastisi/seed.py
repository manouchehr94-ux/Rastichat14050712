"""Seeds a LOCAL, synthetic RastiSi database for the cross-system E2E and prints session cookies as JSON to $SEED_OUT.
Run through `manage.py shell` of a RastiSi checkout pointed at a throw-away database. Never run against real data."""
import json
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from apps.customers.models import Customer
from apps.stores.models import Store, StoreDomain, StoreMembership
from apps.stores.services.platform_code_service import generate_unique_platform_code

User = get_user_model()
PORT = os.environ.get("SI_PORT", "8001")
PW = "e2e-Passw0rd-xyz"
M = StoreMembership.MembershipStatus.ACTIVE
R = StoreMembership.Role if hasattr(StoreMembership, "Role") else None


def role(name):
    return getattr(R, name.upper()) if R and hasattr(R, name.upper()) else name


def make_store(slug, name):
    store = Store.objects.create(name=name, slug=slug, status=Store.Status.ACTIVE,
                                 platform_code=generate_unique_platform_code(), admin_subdomain=slug)
    StoreDomain.objects.create(
        store=store, hostname=f"shop-{slug}.rastisi.localhost", is_primary=True,
        domain_type=StoreDomain.DomainType.CUSTOM_DOMAIN,
        verification_status=StoreDomain.VerificationStatus.VERIFIED, verified_at=timezone.now())
    return store


def make_user(username, **kw):
    return User.objects.create_user(username=username, email=f"{username}@e2e.invalid", password=PW, **kw)


def member(store, user, r):
    StoreMembership.objects.create(store=store, user=user, role=role(r), status=M, accepted_at=timezone.now())


a, b, c = make_store("sa", "فروشگاه الف"), make_store("sb", "فروشگاه ب"), make_store("sc", "فروشگاه ج")
owner = make_user("owner1"); member(a, owner, "owner"); member(b, owner, "owner")   # multi-store owner
admin_a = make_user("admin_a"); member(a, admin_a, "administrator")
manager_a = make_user("manager_a"); member(a, manager_a, "order_manager")
owner_b = make_user("owner_b"); member(b, owner_b, "administrator")   # an administrator of B only (one active owner per store)
owner_c = make_user("owner_c"); member(c, owner_c, "owner")
root = make_user("platform_root", is_staff=True, is_superuser=True)
cust_user = make_user("cust1")
Customer.objects.create(user=cust_user, full_name="مشتری آزمایشی", phone="09120000001")

out = {"stores": {s.slug: {"public_id": str(s.public_id), "name": s.name, "storefront": f"http://shop-{s.slug}.rastisi.localhost:{PORT}",
                           "admin": f"http://{s.admin_subdomain}.rastisi.localhost:{PORT}"} for s in (a, b, c)},
       "platform": f"http://platformadmins.rastisi.localhost:{PORT}", "sessions": {}, "password": PW}
for u in (owner, admin_a, manager_a, owner_b, owner_c, root, cust_user):
    client = Client()
    client.force_login(u)
    out["sessions"][u.username] = {k: v.value for k, v in client.cookies.items()}
out["session_cookie_name"] = settings.SESSION_COOKIE_NAME
with open(os.environ["SEED_OUT"], "w") as fh:
    json.dump(out, fh)
print("seeded", sorted(out["stores"]))
