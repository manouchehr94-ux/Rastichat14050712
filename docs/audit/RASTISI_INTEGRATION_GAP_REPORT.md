# گزارش ممیزی و شکاف‌های ادغام RastiChat × RastiSi (مرحلهٔ A)

> **وضعیت سند:** گزارش مرحلهٔ اول، فقط مستندات. هیچ کد اجرایی، Migration، استقرار یا تغییر روی سرور/ریموت انجام نشده است.
> **مرجع مأموریت:** [Issue #1](https://github.com/manouchehr94-ux/Rastichat14050712/issues/1)
> **تاریخ ممیزی:** ۲۰۲۶-۱۰-۰۴

## ۰. خلاصهٔ مدیریتی

1. RastiChat یک سامانهٔ چت چندمستاجری (Django/DRF/Channels + Next.js + ویجت Vite) با **۵۰۸ تست بک‌اند موفق** است و چت «مشتری ↔ اپراتور فروشگاه» در کد و تست نسبتاً کامل است.
2. **مسیر ۱ (مشتری ↔ فروشگاه):** در سطح کد `PARTIAL`. موتور چت (مکالمه، پیام، رسید، فایل، SLA، صف) هست، اما **هویت مشتری قابل اعتماد نیست** (`external_id` از درخواست عمومی می‌آید و با آزمایش عملی ثابت شد که هر فراخواننده‌ٔ ناشناس می‌تواند مکالمهٔ یک مشتری شناخته‌شده را ادامه دهد و تاریخچه‌اش را بخواند).
3. **مسیر ۲ (ادمین فروشگاه ↔ پشتیبانی):** `BROKEN` از نظر امنیتی. **با آزمایش عملی تأیید شد:** کاربری که ادمین فروشگاه A و فقط اپراتور فروشگاه B است، می‌تواند مکالمات پشتیبانی B را فهرست کند، بخواند، در آن پیام بفرستد، **ویرایش کند و حذف کند**.
4. **مسیر ۳ (مالک پلتفرم → ادمین فروشگاه):** `MISSING`. هیچ API/UI برای آغاز مکالمه از سمت پلتفرم وجود ندارد؛ `POST /api/v1/platform/support/` عملاً **خطای ۵۰۰** (`IntegrityError: workspace_id NULL`) می‌دهد.
5. سایر یافتهٔ P0: هر عضو پشتیبانی پلتفرم می‌تواند مکالمهٔ پشتیبانی را `PATCH`/`DELETE` کند؛ هر اپراتور ساده می‌تواند مکالمهٔ مشتری را `DELETE` کند و `team`/`queue` فروشگاه دیگر را به آن وصل کند.
6. **راستی‌سی از قبل زیرساخت لازم برای هویت امن را دارد** (`StoreMembership` فعال، میزبان‌های جدا برای پلتفرم/پنل فروشنده، بلیت‌های امضاشدهٔ `AdminHandoffTicket`). پیشنهاد: **سرویس چت جدا بماند** و راستی‌سی با **توکن کوتاه‌عمر امضاشده + Provisioning سرور‌به‌سرور** هویت را به چت معرفی کند. ادغام پایگاه داده الزامی نیست.
7. **درخواست تأیید** پیش از Phase B در بخش ۱۲.

---

## ۱. روش، نسخه‌ها و محدودیت‌ها

### ۱.۱ نسخه‌های بررسی‌شده

| پروژه | شاخه | Commit | یادداشت |
|---|---|---|---|
| Rastichat14050712 | `main` | `d56a2c1` (۴۶۵ فایل) | ریشهٔ تمیز؛ تاریخچهٔ قدیمی منتقل نشده |
| Rastisi6-14040616 | `main` | `b48d40e` (نسخهٔ سرور) **و** HEAD فعلی `9325a4a` | `b48d40e` **جد** `9325a4a` است؛ **۲۳۷ commit** از آن جلوتر رفته (شامل Merge PR #20). کد روی GitHub لزوماً با سرور برابر نیست. |

### ۱.۲ ریموت مرجع راستی‌سی — **تعیین‌شده (بر اساس بررسی مستقیم مالک روی سرور)**

مالک روی سرور فعال بررسی کرد (این محیط به سرور دسترسی ندارد؛ اطلاعات زیر **گزارش مالک** است، نه مشاهدهٔ مستقیم):

| مورد | مقدار |
|---|---|
| مسیر | `/opt/rastisi-next` |
| `origin` (fetch و push) | `https://github.com/manouchehr94-ux/Rastisi6-14040616.git` |
| ریموت `rastisi5` | **وجود ندارد** |
| شاخه / commit | `main` / `b48d40e1d127093ab388db72895d1e623a838b60` |
| وضعیت Git | `## main...origin/main`، بدون تغییر |

**نتیجه:** مخزن `Rastisi6-14040616` مرجع است و جملهٔ «ریموت مرجع `rastisi5`» در `CLAUDE.md` راستی‌سی **منسوخ/ناسازگار با سرور** به‌نظر می‌رسد. اصلاح آن فایل کار یک PR جدا در راستی‌سی (پس از تأیید مالک) است؛ هیچ ریموتی تغییر نکرد و کارهای این مخزن هم به راستی‌سی push نمی‌کنند.

### ۱.۲.۱ اختلاف سرور (`b48d40e`) با `main` فعلی GitHub (`9325a4a`)

`b48d40e` جد `9325a4a` است (خطی؛ بدون واگرایی). اختلاف: **۲۳۷ commit، ۲۱۵۶ فایل، +۲۶۱٬۲۹۱/−۳٬۴۲۷ خط**. بررسی با `git diff`:

- **سطح هویت/ایزولاسیون تقریباً دست‌نخورده:** `apps/stores/authorization.py` (+۱۴ خط: دو کلید مجوز `ORDER_CONFIRM_COD_PAYMENT` و `STORE_DELETE`)، `apps/portal/platform_admin_views.py` (+۲۰ خط: override پلن)، `shop_core/settings.py` (+۱ خط: `apps.engagement`). **`handoff_service.py`، `resolution.py`، `middleware.py` و مدل‌های `stores`/`portal` بدون تغییر‌اند و هیچ Migration در `stores` یا `portal` نیست.**
- **تغییر عمده در حوزهٔ تجارت و تعامل:** ۲۷ فایل Migration در ۹ اپ (`orders`: ۹، `customers`: ۴، `core`: ۳، `engagement`: ۳، `cart`: ۲، `notifications`: ۲، …). `customers.Customer` فیلدهای رضایت‌نامهٔ تبلیغاتی گرفته و `notifications.NotificationOutbox` رویدادمحور شده است.
- **اثر بر ادغام:**
  1. قرارداد هویت این گزارش (بر پایهٔ `Store.public_id`، `StoreMembership`، `Customer`) بر هر دو نسخه معتبر است؛ شناسه‌ها تغییر نکرده‌اند.
  2. هر کد سمت راستی‌سی باید روی **`main` فعلی** توسعه و تست شود، نه روی `b48d40e`.
  3. استقرار نهایی راستی‌سی روی سرور مستلزم رساندن سرور به نسخهٔ جدید (۲۷ Migration تجارت/تعامل) است؛ این یک **تصمیم و عملیات جدا و مستقل از چت** است و بدون اجازهٔ صریح مالک انجام نمی‌شود. تا آن زمان، سرور به‌خاطر چت به‌روزرسانی **نمی‌شود**؛ ادغام چت پشت Feature Flag خاموش و در شاخهٔ مجزا می‌ماند.
  4. ریسک: مسیرهای `orders`/`customers`/`notifications` که آداپتور آیندهٔ سفارش‌ها یا اعلان‌ها به آن‌ها وصل می‌شوند، همین‌ها بیشترین تغییر را داشته‌اند؛ آداپتور باید فقط از لایهٔ سرویس پایدار بخواند.

### ۱.۳ آنچه اجرا شد

محیط ایزوله: PostgreSQL 16 و Redis محلی موقت، بدون هیچ دادهٔ واقعی.

| بررسی | دستور | نتیجه |
|---|---|---|
| Django check | `python manage.py check` | بدون مشکل |
| Migration drift | `python manage.py makemigrations --check --dry-run` | `No changes detected` |
| تست‌های بک‌اند | `python manage.py test` | **`Ran 508 tests … OK`** (۴۴۹ ثانیه) |
| Bandit (`-ll`) | `bandit -r . -x tests,migrations -ll` | بدون یافتهٔ متوسط+ |
| ویجت | `npm ci; tsc --noEmit; vitest run` | typecheck OK؛ **۲۳/۲۳ تست** |
| operator-dashboard | `tsc --noEmit; vitest run` | **۹۴/۹۴ تست** (۹ فایل) |
| platform-dashboard | `tsc --noEmit; vitest run` | **۱۹/۱۹ تست** (۲ فایل) |
| سه آزمایش عملی (probe) | تست‌های موقت و حذف‌شده (برای ایزوله بودن در مخزن commit نشده‌اند) | نتایج در بخش ۴ |

**اجرا نشد (`UNVERIFIED`):** تست‌های Playwright E2E (`e2e/`)، تست مرورگر/موبایل/RTL واقعی، Lint فرانت، `check --deploy` روی تنظیمات staging، **همهٔ تست‌های راستی‌سی** (وابستگی‌ها نصب نشد؛ فقط کد خوانده شد)، و هر چیز مربوط به سرور زنده. فایل `PHASE1_BASELINE.txt` خودش می‌گوید «Manual Browser Verification: Pending».

> یادداشت صداقت: یک بار تست آزمایشی من هم‌زمان با اجرای کامل روی همان دیتابیس تست اجرا شد؛ اجرای کامل با `OK` پایان یافت، اما برای اطمینان کامل پیشنهاد می‌شود CI همین عدد را مستقل تأیید کند.

### ۱.۴ سطوح شواهد در این گزارش

`کد` = در سورس دیده شد؛ `تست` = تست موجود و موفق؛ `آزمایش` = من محلی اجرا و رفتار را دیدم؛ `تولید` = **هیچ‌کدام از موارد این گزارش در تولید راستی‌آزمایی نشده است**.

### ۱.۵ وضعیت CI (PR #2، run `37202792264`) — **دو Job ناموفق، نه به‌خاطر کد این PR**

| Job | نتیجه | علت (از لاگ‌های GitHub + بازتولید محلی) |
|---|---|---|
| widget, operator-dashboard, platform-dashboard, secret-scan | ✅ | — |
| `docker-build` | ❌ | `scripts/staging/generate-ci-env.sh: Permission denied` (exit 126). در Git **همهٔ ۱۵ فایل `.sh`** (`git ls-files -s`) با mode `100644` ثبت شده‌اند؛ بیت اجرایی هنگام انتقال از ویندوز از دست رفته است. `Dockerfile.prod` خودش `chmod +x` می‌زند، اما workflow و اسکریپت‌های عملیاتی (`deploy.sh`، `rollback.sh`، `backup.sh`…) مستقیماً اجرا می‌شوند. |
| `backend` | ❌ | تست‌ها موفق؛ مرحلهٔ `pip-audit` **۱۰ آسیب‌پذیری** در ۲ بسته: `Django 4.2.30` (۸ مورد: PYSEC-2026-3717، PYSEC-2026-4035، GHSA-923m-gv2p-w5qp، GHSA-h7pc-vwp9-298g، GHSA-8cjm-8mp7-r2xf، GHSA-3h9f-r86x-qvjx، GHSA-crhf-3pfg-w68w، GHSA-8qcx-xf44-272x) و `djangorestframework 3.15.2` (۲ مورد: PYSEC-2026-3827/3828). بازتولید محلی با `pip-audit -r requirements.txt` همین ۱۰ مورد را داد. |

**نکتهٔ مهم دربارهٔ ارتقا:** هیچ‌کدام از ۸ آسیب‌پذیری Django در شاخهٔ ۴.۲ رفع نشده‌اند؛ نسخه‌های رفع‌شده فقط `5.2.15–5.2.17` و `6.0.6–6.0.8` هستند. یعنی ارتقای **جهشی (4.2 → 5.2 LTS)** لازم است، نه یک patch ساده. DRF باید به `3.17.2` برود. (برنامه در §۱۲، مورد ۱.)

**ارزیابی تهدید برای نسخهٔ فعال چت:** هر استقرار فعلی که روی `Django 4.2.30` / `DRF 3.15.2` اجرا شود در معرض همین ۱۰ advisory است. شدت و قابلیت بهره‌برداری هر مورد در این محیط (بدون دسترسی به شرح کامل advisory) **راستی‌آزمایی نشد** (`UNVERIFIED`). **توصیهٔ مهار کم‌ریسک بدون تغییر عملیاتی:** (الف) مالک شرح advisoryها را برای مسیرهایی که چت واقعاً استفاده می‌کند (آپلود، فرم/پارسر، Admin، `ModelViewSet`) بررسی کند؛ (ب) تا ارتقا، `ADMIN_URL` را از `/admin/` تغییر دهد و دسترسی Admin را در Nginx محدود به IP کند؛ (ج) ارتقا پس از تست کامل و فقط با تأیید مالک. **هیچ اقدام عملیاتی انجام نشد.**

---

## ۲. معماری فعلی

### ۲.۱ RastiChat

```
ویجت (Vite/TS)  ──REST/WS──┐
operator-dashboard (Next) ──┼──► Django/DRF + Channels (Daphne) ──► PostgreSQL
platform-dashboard (Next) ──┘                │                    └► Redis (channel layer + rate limit)
                                             └► نگینکس (/media/ عمومی با نام غیرقابل حدس)
```

سلسله‌مراتب مستاجری: `Platform → Workspace → Project(public_key UUID)`؛ `Visitor` زیر `Project`؛ `Conversation(type=CUSTOMER|PLATFORM_SUPPORT)` زیر `Workspace`.

نقش‌ها: `PlatformMembership` (OWNER/ADMIN/SUPPORT_AGENT)، `WorkspaceMembership` (OWNER/ADMIN/OPERATOR) — `backend/platforms/models.py`، `backend/workspaces/models.py`.

احراز هویت: ادمین/اپراتور با JWT (Access ۶۰ دقیقه، Refresh یک روز، `config/settings.py` بخش `SIMPLE_JWT`) در `localStorage`؛ مشتری با `VisitorSession.token` (UUID بدون انقضا).

### ۲.۲ RastiSi

- **تعریف هویت:**
  - فروشگاه: `apps.stores.models.Store` (`public_id` UUID، `admin_subdomain`، `status`).
  - ادمین/اپراتور فروشگاه: `StoreMembership(store, user, role, status)` با نقش‌های `owner/administrator/catalog_manager/order_manager/content_editor/analyst`؛ **فقط `ACTIVE` معتبر است** (`apps/stores/authorization.py`، `get_active_membership`).
  - مشتری: `apps.customers.models.Customer` (OneToOne به User، **`phone` یکتا در سطح کل سامانه**، نه در سطح فروشگاه). سفارش‌ها `store` دارند.
  - مالک پلتفرم: **فقط `is_staff and is_superuser`** در میزبان جدا (`apps/portal/platform_admin_views.py::_is_platform_staff`). **نقش «پشتیبانی پلتفرم» جدا در راستی‌سی وجود ندارد.**
- **ایزوله‌سازی:** `StoreResolutionMiddleware` فروشگاه را از Host تعیین می‌کند؛ `PlatformHostRoutingMiddleware` میزبان‌های `RASTISI_PLATFORM_HOSTS` و `RASTISI_PLATFORM_ADMIN_HOSTS` را به URLconfهای جدا می‌برد؛ پنل فروشنده (`/admin-portal/`) روی `admin_subdomain` همان فروشگاه و با `staff_required` (عضویت ACTIVE دقیقاً همان فروشگاه) محافظت می‌شود. Django `/admin/` فقط برای superuser.
- **کوکی/سشن:** `SESSION_COOKIE_DOMAIN` عمداً تنظیم نشده (کوکی host-only)؛ جابه‌جایی بین میزبان‌ها با **`AdminHandoffTicket` یک‌بارمصرف ۶۰۰ثانیه‌ای** و `build_admin_return_token` (امضاشده با `django.core.signing`) انجام می‌شود (`apps/portal/services/handoff_service.py`). این الگو **دقیقاً همان چیزی است که برای SSO چت لازم است.**
- **اعلان‌ها:** `apps.notifications.NotificationOutbox` (in_app/sms/email) با `store`، `recipient_user`.
- `chatchat` در راستی‌سی **Handle رزرو** است («سرویس همتای پلتفرم»، `apps/stores/hostnames.py:173`).

### ۲.۳ اتصال امروز

**هیچ اتصالی وجود ندارد.** جستجو در راستی‌سی هیچ ارجاع کدی به RastiChat نشان نداد (فقط رزرو نام `chatchat`).

---

## ۳. جدول امکانات RastiChat

راهنما: ✅ = `COMPLETE` (کد + تست موفق؛ **نه** تأیید تولید) · ◐ = `PARTIAL` · ✖ = `MISSING` · ✱ = `BROKEN` · ؟ = `UNVERIFIED`

| # | قابلیت | وضعیت | شواهد (فایل / تست) | یادداشت |
|---|---|---|---|---|
| 1 | مدل داده Platform/Workspace/Project/Visitor/Conversation/Message | ✅ | `*/models.py`؛ drift ندارد | `Conversation.workspace` غیرnull؛ پشتیبانی بدون `project` |
| 2 | ایجاد/ادامهٔ مکالمهٔ مشتری (ویجت) | ◐ | `views.py:365` `StartCustomerChatView`؛ تست‌های `tests.py` | مکالمهٔ بسته‌شده دوباره باز نمی‌شود (خط `if not created and conv.status=='CLOSED'` بی‌اثر است چون `status=OPEN` در lookup است)؛ مکالمهٔ جدید ساخته می‌شود |
| 3 | پیام مشتری REST/WS + idempotency | ✅ | `consumers.py:81`، `unique_together(conversation, client_message_id)` | WS نرخ‌محدود (Redis) |
| 4 | پاسخ اپراتور + SLA/اتوماسیون | ✅ | `views.py:412`، `consumers.py:151` | |
| 5 | رسید خواندن | ✅ | `MessageReceipt`؛ `views.py:471` | |
| 6 | نشانگر تایپ | ✅ | `consumers.py` `typing.indicator` | فقط مشتری↔اپراتور؛ پشتیبانی ندارد |
| 7 | تصویر/صدا | ✅ | `media_validation.py`، `tests_media_security.py` | نام تصادفی؛ **`/media/` در نگینکس بدون احراز** |
| 8 | واگذاری/انتقال/صف/اولویت/SLA/Escalate | ✅ | `views.py:120-200`؛ `tests_team_ops.py`، `tests_escalation_capacity.py` | |
| 9 | پاسخ‌آماده/ماکرو/اتوماسیون/دانش‌نامه | ✅ | `collaboration`, `macros`, `automations`, `knowledge_base` | بازبینی عمیق انجام نشد |
| 10 | اطلاعات مشتری/سفارش/محصول | ◐ | `docs/architecture/COMMERCE_INTEGRATION.md` | **اسنپ‌شات محلی؛ اتصال زنده به راستی‌سی وجود ندارد** (تأیید شد) |
| 11 | اعلان درون‌برنامه‌ای اپراتور | ◐ | `notifications` (Mention/SLA/Automation/Assignment) | **برای پیام جدید پشتیبانی هیچ اعلانی ساخته نمی‌شود** |
| 12 | حضور (Presence) اپراتور | ✅ | `accounts/presence.py` | |
| 13 | رابط فارسی/RTL | ؟ | `layout.tsx: lang="fa" dir="rtl"`، ویجت `direction: rtl` | بدون مرورگر راستی‌آزمایی نشد |
| 14 | موبایل/Responsive | ؟ | — | بررسی نشد |
| 15 | WebSocket reconnect | ◐ | ویجت: `setTimeout(connectWebSocket, 2000)` | بدون backoff؛ پس از قطع، پیام‌های ازدست‌رفته با REST دوباره بارگذاری می‌شوند؟ **`UNVERIFIED`** |
| 16 | Provisioning خودکار Workspace/Project | ✖ | — | فقط `seed_data.py` |
| 17 | SSO/هویت امضاشده برای مشتری و ادمین | ✖ | — | تنها `/auth/login/` ایمیل+رمز |
| 18 | اعمال `Project.allowed_domains` | ✖ | فقط تعریف/سریالایزر (`projects/*`)؛ هیچ مصرفی | فیلد تزئینی است |
| 19 | انقضای نشست مشتری | ✖ | `VisitorSession.expires_at` هرگز مقدار نمی‌گیرد/بررسی نمی‌شود | |
| 20 | بستن/بازگشایی مکالمهٔ پشتیبانی | ✖ | تست‌های ۲۶/۲۷ فقط `pass` | وضعیت read-only در serializer؛ هیچ endpoint ای نیست |
| 21 | فایل/عکس در پشتیبانی | ✖ | `WorkspaceSupportViewSet` فقط `content` متنی | |
| 22 | صفحه‌بندی تاریخچهٔ پشتیبانی | ✖ | تست ۲۲ `pass` | `messages` همهٔ پیام‌ها را برمی‌گرداند |

---

## ۴. یافته‌های امنیتی و ایزولاسیون (ادعاهای §۴ مأموریت)

شدت: **P0** = مانع ادغام و باید قبل از هر کار دیگر رفع شود · **P1** = مهم · **P2** = بهبود.

### SEC-01 — ادمین‌A/اپراتور‌B به پشتیبانی B دسترسی کامل دارد — **P0 · BROKEN · آزمایش‌شده**

- **کد:** `views.py:561-566`: `WorkspaceSupportViewSet.permission_classes=[IsWorkspaceAdmin]` (`common/permissions.py`: «ادمین در **هر** فروشگاهی») و `get_queryset` فقط بر عضویت در workspace فیلتر می‌کند.
- **آزمایش:** کاربر u = ادمین A + اپراتور B؛ مکالمهٔ پشتیبانی B:
  - لیست شامل مکالمهٔ B بود (`True`)؛ `GET messages` → ۲۰۰؛ `send_message` → **۲۰۱**؛ `PATCH` → ۲۰۰ و موضوع به `HACKED` تغییر کرد؛ `DELETE` → **۲۰۴ و مکالمه حذف شد.**
- **اثر:** هر کارمند با نقش پایین در یک فروشگاه و نقش مدیریتی در فروشگاه دیگر، پشتیبانی مالی/حساس فروشگاه سوم را می‌خواند و پاک می‌کند.
- **تست موجود:** `test_6_ws_admin_cannot_see_other_ws` فقط `pass` است (`tests_support.py:76`)؛ یعنی این خلأ بدون پوشش است.
- **راهکار:** مجوز سطح شیء با `user_has_workspace_role(user, conv.workspace, [OWNER, ADMIN])` (الگوی موجود `IsWorkspaceAdminOfObject`)، `queryset` فقط روی workspaceهایی که کاربر در آن‌ها ادمین است، حذف `ModelViewSet` به `GenericViewSet` با فقط `list/retrieve/create` + actionهای صریح.

### SEC-02 — ساخت مکالمهٔ پشتیبانی با `.first()` — **P0 · BROKEN · آزمایش‌شده**

- **کد:** `views.py:568-576`: `request.user.workspace_memberships.filter(role__in=[...]).first()` (بدون ترتیب).
- **آزمایش:** مدیر هر دو فروشگاه A و B با `{"workspace_id": B}` → مکالمه **در A** ساخته شد؛ `workspace_id` نادیده گرفته می‌شود.
- **راهکار:** `workspace_id` اجباری، اعتبارسنجی با `resolve_operator_workspace` + `require_workspace_admin` (هر دو در `common/` موجودند)؛ در ادغام، فروشگاه از **ادعای امضاشدهٔ راستی‌سی** می‌آید، نه بدنهٔ درخواست مرورگر.

### SEC-03 — `PlatformSupportViewSet` به‌عنوان `ModelViewSet` — **P0 · BROKEN · آزمایش‌شده**

- **آزمایش:** با نقش ضعیف‌ترین `PLATFORM_SUPPORT_AGENT`: `PATCH` → ۲۰۰ (موضوع تغییر کرد)؛ `DELETE` → **۲۰۴ (مکالمه حذف شد)**؛ `POST` (ایجاد) → **۵۰۰ `IntegrityError`**؛ `reply` بدون `client_message_id` → **۵۰۰ `KeyError`** (`views.py:647` از `request.data['client_message_id']`).
- **کد:** `assign` (`views.py:629-639`) مقدار `conv.assigned_to` را ست می‌کند ولی `conv.save()` ندارد → **تخصیص ذخیره نمی‌شود** (تأیید از روی کد؛ تست ۱۵ فقط تعداد `Assignment` را می‌شمارد). `reply` ردّ پیام تکراری ندارد (`unique_together` ⇒ ۵۰۰ در تکرار).
- **کد:** `PlatformSupportViewSet` هیچ تفکیک بین `PLATFORM_OWNER/ADMIN/SUPPORT_AGENT` ندارد؛ هر سه یکسان.
- **راهکار:** `GenericViewSet` با actionهای صریح، مجوز نقش‌محور (حذف فقط Owner/Admin یا اصلاً ممنوع)، اعتبارسنجی ورودی با serializer، `assign` با ذخیره/Audit/رویداد.

### SEC-04 — مکالمهٔ مشتری: حذف توسط هر اپراتور و FK بین‌فروشگاهی — **P0/P1 · آزمایش‌شده**

- `CustomerConversationViewSet` یک `ModelViewSet` است. **آزمایش:** اپراتور ساده (نه ادمین) `DELETE` مکالمهٔ مشتری → **۲۰۴ و حذف**؛ `PATCH {team, queue}` با شناسهٔ تیم/صف **فروشگاه B** برای مکالمهٔ A → ۲۰۰ و FK واقعاً به B وصل شد (`ConversationSerializer` فیلدهای `team`/`queue` را قابل‌نوشتن گذاشته).
- نشت جزئی: تیم/صف B (نام) در پاسخ A دیده می‌شود؛ ممکن است اتوماسیون/SLA B را تحریک کند.
- **راهکار:** حذف `destroy/create/update` عمومی؛ `team/queue` فقط‌خواندنی یا اعتبارسنجی هم‌workspace بودن (الگوی تست‌های `common/tests_workspace_permissions.py` برای سایر منابع وجود دارد و موفق است؛ این منبع از قلم افتاده).

### SEC-05 — هویت مشتری و `external_id` — **P0 (برای ادغام) · آزمایش‌شده**

- **کد:** `visitors/views.py:24-29`: `get_or_create(project, external_id)` با ورودی عمومی؛ `visitors/serializers.py:59`.
- **آزمایش:** دو فراخوان ناشناس با `external_id=cust-42` (اولی «مشتری»، دومی «مهاجم»): **هر دو به همان Visitor** رسیدند؛ مهاجم همان مکالمه را باز کرد و **تاریخچهٔ خصوصی** («my private order question») را خواند.
- **شدت:** `project_key` عمومی است و ویجت فعلی `external_id` نمی‌فرستد (`packages/widget/src/main.ts:450`) پس در **وضعیت امروز** فقط مستقیماً با API قابل سوءاستفاده است، اما **هر ادغام ساده‌ای که `external_id` را از مرورگر بگیرد این حفره را فعال می‌کند.**
- **راهکار:** `external_id` فقط از **ادعای امضاشدهٔ سرور راستی‌سی** (بخش ۸) پذیرفته شود؛ بدون امضا فقط مهمان با Visitor مجزا. شناسه باید دامنهٔ فروشگاه داشته باشد (`store_public_id:customer_id`) چون `Customer.phone` در راستی‌سی سراسری است.

### SEC-06 — توکن‌ها در URL و WebSocket — **P1 · تأیید از روی کد**

- `config/routing.py:6-9`: JWT دسترسی (۶۰ دقیقه) و `session_token` در **مسیر URL**؛ همچنین `GET widget/conversations/<id>/messages/?session_token=…` (query string). این مقادیر معمولاً در لاگ نگینکس (`access_log`، `deploy/nginx/sites/*.template`)، پراکسی‌ها و Referrer ثبت می‌شوند — **ثبت واقعی در سرور `UNVERIFIED`**.
- `consumers.py:170-171,242-243`, `notifications/consumers.py:33-34`: `User.objects.get(id=...)` **بدون بررسی `is_active`**؛ کاربر غیرفعال با JWT قبلی تا ۶۰ دقیقه به WS وصل می‌ماند. هیچ بازبینی مجدد عضویت پس از اتصال نیست.
- `localStorage` برای JWT در هر دو داشبورد (`lib/api.ts`) → در معرض XSS (CSP backend تنظیم شده؛ CSP فرانت بررسی نشد).
- **راهکار:** تیکت WS **کوتاه‌عمر (۳۰–۶۰ ثانیه)، یک‌بارمصرف، متصل به (کاربر، مکالمه/محدوده)** که با یک `POST` احراز‌شده گرفته می‌شود و در `Sec-WebSocket-Protocol` یا پیام اول می‌آید، نه URL؛ بررسی `is_active` و عضویت در `connect`.

### SEC-07 — نشست مشتری بدون انقضا؛ `allowed_domains` بی‌اثر — **P1**

- `VisitorSession.expires_at` هرگز تنظیم/اعمال نمی‌شود (`grep` تأیید کرد). `Project.allowed_domains` هیچ مصرفی ندارد؛ تنها محافظ مبدأ، لیست سراسری `CORS_ALLOWED_ORIGINS` است (اعمال‌شده روی WS هم، `config/asgi.py`). برای SaaS با دامنهٔ سفارشی هر فروشگاه، این لیست سراسری **مقیاس‌پذیر نیست** و باید پویا از دامنه‌های Store مجاز راستی‌سی ساخته شود.

### SEC-08 — داده/حساب‌های Seed — **P1 · کد**

- `backend/seed_data.py` حساب‌های `pass1234` می‌سازد (`platform@rasti.com` با `is_superuser`، ادمین/اپراتور با `is_staff=True`)؛ `README.md` همین‌ها را تبلیغ می‌کند. **entrypoint آن را خودکار اجرا نمی‌کند** (`docker-entrypoint.sh` فقط `seed-staging` را دارد که رمز تصادفی تولید می‌کند؛ تست `common/tests_seed_staging.py` این را می‌پوشاند). خطر: اجرای دستی/اشتباه `seed_data.py` روی تولید. **راهکار:** گارد `ENVIRONMENT` در اسکریپت، و حذف `is_staff` از حساب‌های نمونه.

### SEC-09 — فایل‌های `/media/` — **P2**

- نگینکس `/media/` را بدون احراز سرو می‌کند (نام تصادفی ۳۲ هگز). این محدودیت در مستندات خود پروژه (`STAGING_DEPLOYMENT.md`) هم ثبت شده است. برای پیوست‌های خصوصی مشتری توصیه: URL امضاشده/ `X-Accel-Redirect` با بررسی عضویت.

### SEC-10 — سایر

- `ConversationSerializer` به همهٔ اپراتورها `visitor.email/mobile` را نشان می‌دهد؛ برای ادغام باید فقط داده‌های لازم (حداقل PII) منتقل شود.
- بدون تست‌های مجوز سطح شیء برای `PlatformSupportViewSet` در برابر نقش‌های سه‌گانهٔ پلتفرم.
- نرخ‌محدودیت: WS مشتری دارد؛ WS پشتیبانی/داشبورد و REST پشتیبانی ندارند (spam).

---

## ۵. وضعیت دقیق سه مسیر پیام‌رسانی

| نیاز | مسیر ۱: مشتری↔فروشگاه | مسیر ۲: ادمین↔پلتفرم | مسیر ۳: پلتفرم→ادمین |
|---|---|---|---|
| ورودی در UI | ویجت (✅ کد؛ ؟ مرورگر) | `operator-dashboard/support` (✅ کد) | **✖ ندارد** (`platform-dashboard/inbox` فقط پاسخ؛ فرم «تیکت جدید» نیست) |
| هویت معتبر | **✱ جعل‌پذیر** (SEC-05) | JWT معتبر ولی **انتخاب فروشگاه ✱** (SEC-01/02) | **✖** |
| ایجاد/ادامه | ✅ | ◐ (با `.first()`) | **✱ ۵۰۰** |
| ارسال/دریافت | ✅ REST+WS | ◐ REST+WS (WS بدون ثبت وضعیت/رسید) | ✖ |
| جداسازی فروشگاه‌ها | ✅ برای اپراتور (تست‌ها) / ✱ FK (SEC-04) | **✱** | **✖** |
| اعلان | ◐ (SLA/تخصیص؛ نه پیام جدید به پلتفرم) | **✖** | **✖** |
| خوانده‌نشده/رسید | ✅ | ◐ (REST `mark_read`) | ✖ |
| بستن/تاریخچه | ✅ بستن | **✖** | **✖** |
| تست پوشش | ۵۰۸ تست کلی | ۲۹ تست (۶ placeholder) | ندارد |
| تأیید مرورگر/تولید | **UNVERIFIED** | **UNVERIFIED** | — |

> **نتیجه:** مسیر ۱ نزدیک به آماده (با اصلاح هویت)؛ مسیر ۲ نیازمند بازنویسی ایمن کنترلر؛ مسیر ۳ از صفر ساخته می‌شود.

---

## ۶. تست‌ها: وضعیت و نواقص

- ۵۰۸ تست بک‌اند موفق (تعداد `def test_` در فایل‌های `tests*.py` نیز ۵۰۸ است).
- `conversations/tests_support.py`: ۶ تست `pass` — `test_6` (ایزولاسیون بین‌فروشگاهی)، `test_22` (صفحه‌بندی)، `test_24–27` (انتقال وضعیت/بستن/بازگشایی) (خطوط ۷۶، ۱۸۰، ۱۹۱، ۱۹۴، ۱۹۷، ۲۰۰).
- `test_7_pl_support_cannot_access_customer` نامش با رفتار مطابقت ندارد (فقط طول لیست پشتیبانی را می‌سنجد، به مکالمهٔ مشتری دسترسی نمی‌دهد).
- تست‌های مفید موجود: ایزولاسیون workspace برای teams/queues/SLA/quick-replies (`common/tests_workspace_permissions.py`)، مدیا، throttling، Origin WS.
- **نبود:** تست «ادمین‌A/اپراتور‌B»، مدیر چندفروشگاهی، نقش‌های پلتفرم، DELETE/PATCH ناخواسته، `external_id` جعلی، `is_active` در WS، تست مرورگر برای همهٔ مسیرها.

---

## ۷. گزینه‌های ادغام و معماری پیشنهادی

| گزینه | شرح | مزایا | معایب/ریسک |
|---|---|---|---|
| **A (پیشنهادی)** | سرویس چت جدا + **SSO امضاشده کوتاه‌عمر** + **Provisioning سرور‌به‌سرور** + نمایش در UI راستی‌سی (iframe/ویجت + BFF سبک) | کمترین ریسک؛ بدون ادغام DB؛ استقلال استقرار؛ بازگشت ساده؛ منطبق با الگوی موجود `AdminHandoffTicket` | دو پایگاه داده؛ نیاز به هماهنگی شناسه‌ها؛ نیاز به CSP/`frame-ancestors`/CORS دقیق |
| B | Proxy/BFF کامل داخل Django راستی‌سی (تمام REST/WS از میزبان راستی‌سی) | یک مبدأ؛ کوکی/CSRF ساده‌تر | نیازمند پروکسی WS (وجود ASGI/Channels در راستی‌سی **بررسی نشد**)؛ بار اضافه روی راستی‌سی زنده |
| C | انتقال چت به درون Django راستی‌سی | یکپارچگی کامل | بازنویسی بزرگ، Migration روی DB زنده، ریسک بالا، بر خلاف خواستهٔ شما |

**انتخاب:** گزینه A، با این جزئیات: راستی‌سی (سرور) **ادعای هویت** را امضا می‌کند؛ مرورگر هرگز شناسهٔ فروشگاه/مشتری را به‌عنوان مدرک نمی‌فرستد؛ چت فقط ادعای امضاشده و Provisioning سرور‌به‌سرور را قبول می‌کند. WebSocket چت مستقیماً بین مرورگر و سرویس چت می‌ماند (نیازی به پروکسی WS در Django نیست).

---

## ۸. قرارداد پیشنهادی هویت و API

> **این‌ها پیشنهادند، نه APIهای موجود.** هیچ شناسه‌ای جعل نشده؛ همهٔ شناسه‌ها از مدل‌های واقعی راستی‌سی/چت گرفته شده‌اند.

### ۸.۱ شناسه‌ها

| مفهوم | شناسهٔ مرجع | منبع حقیقت |
|---|---|---|
| فروشگاه | `Store.public_id` (UUID) | راستی‌سی |
| Workspace چت | `Workspace.external_id = "rastisi:store:<public_id>"` (+ ایندکس یکتا) | چت (ساخته‌شده توسط Provisioning) |
| Platform چت | `Platform.external_id = "rastisi"` (موجود و یکتا) | چت |
| مشتری | `Visitor.external_id = "<store_public_id>:<customer_id>"` (دامنه‌دار؛ `Customer.phone` سراسری است) | راستی‌سی |
| ادمین فروشگاه | `User.email` فعلی چت ← پیشنهاد: فیلد `external_id="rastisi:user:<id>"` (Migration) | راستی‌سی |
| مالک پلتفرم | کاربر `is_staff & is_superuser` در میزبان پلتفرم‌ادمین ← نگاشت به `PlatformMembership` | راستی‌سی |

### ۸.۲ توکن SSO (امضاشده، کوتاه‌عمر)

JWT/`signing` با کلید **اشتراکی فقط سرور‌به‌سرور** (متغیر محیطی، هرگز commit نشود)، `exp ≤ ۶۰ثانیه`، یک‌بارمصرف (`jti` در Redis)، `aud=rastichat`:

```json
{ "iss": "rastisi", "aud": "rastichat", "jti": "...", "exp": ...,
  "kind": "customer|store_admin|platform_owner",
  "store": "<Store.public_id>",             // سمت سرور راستی‌سی، از Host/membership فعال
  "sub": "<user id | customer id | null>",
  "role": "owner|administrator|...",        // از StoreMembership ACTIVE
  "name": "...", "scope": ["chat:customer"|"chat:store_support"|"chat:platform_support"] }
```

ساخت توکن **فقط در سمت راستی‌سی** و پس از بررسی Host/`StoreResolution`، سشن و `StoreMembership(ACTIVE)` (برای ادمین) یا `request.user.customer_profile` (برای مشتری). مهمان: بدون `sub` ⇒ Visitor ناشناس مجزا.

### ۸.۳ APIهای پیشنهادی سمت چت

| متد | مسیر | احراز | کار |
|---|---|---|---|
| POST | `/api/v1/integrations/rastisi/stores/` | HMAC سرور‌به‌سرور | Provisioning idempotent (upsert) Workspace+Project با `external_id`؛ هرگز رکورد تکراری نمی‌سازد |
| POST | `/api/v1/sso/exchange/` | توکن SSO | تبدیل به نشست چت (کوتاه‌عمر) برای نوع مربوط؛ بررسی `jti` |
| POST | `/api/v1/ws-ticket/` | نشست چت | تیکت WS یک‌بارمصرف ۳۰ثانیه‌ای مقید به مکالمه |
| GET/POST | `/api/v1/support/` | ادمین فروشگاه (نقش دقیق در **همان** workspace) | لیست/ایجاد؛ `workspace` از ادعای امضاشده |
| POST | `/api/v1/platform/support/start/` | `PLATFORM_OWNER/ADMIN` (+ مجوز صریح) | **آغاز مکالمه با فروشگاه انتخابی**، idempotent بر `(workspace, subject_key)` |
| POST | `/api/v1/platform/support/<id>/{reply,assign,close,reopen}/` | نقش پلتفرم | با Audit و رویداد |

### ۸.۴ Handshake WS

`POST /ws-ticket/` → `{ticket}` → اتصال `wss://…/ws/…/` + ارسال `{type:"auth",ticket}` به‌عنوان اولین پیام (یا `Sec-WebSocket-Protocol`)؛ سرور ticket را مصرف و عضویت/`is_active` را مجدداً می‌سنجد.

---

## ۹. تغییرات احتمالی مدل و Migration (فقط پیشنهاد)

1. `Workspace`: ایندکس یکتای جزئی روی `(platform, external_id)` (فیلد موجود است).
2. `Project`: فیلد وضعیت اتصال/`store_public_id` یا استفاده از `Workspace.external_id`؛ تبدیل `allowed_domains` به منبعی که از دامنه‌های تأییدشدهٔ Store همگام می‌شود.
3. `User`: فیلد `external_id` (nullable، یکتا) برای نگاشت با کاربر راستی‌سی.
4. `Visitor`: `external_id` همچنان؛ **ایندکس یکتا جزئی** `(project, external_id)` برای مقادیر غیرتهی؛ فیلد `identity_verified` (bool).
5. `VisitorSession`: اعمال `expires_at` (بدون Migration ساختاری؛ فقط منطق).
6. `Conversation`: نوع/کلید برای مکالمهٔ آغازشده از پلتفرم (`initiated_by_side`, `subject_key`) و یکتایی `(workspace, type=PLATFORM_SUPPORT, subject_key)` برای idempotency؛ وضعیت `closed/resolved` برای پشتیبانی.
7. جدول `SsoTokenUse(jti, used_at)` یا Redis (ترجیحاً Redis بدون Migration).
8. همهٔ Migrationها افزایشی و معکوس‌پذیر؛ **اجرا فقط روی محیط آزمایشی**.

---

## ۱۰. برنامهٔ اجرایی مرحله‌بندی‌شده

| اولویت | PR | محتوا | معیار پذیرش (تست) |
|---|---|---|---|
| **P0-1** | امن‌سازی پشتیبانی فروشگاه | SEC-01/02: مجوز سطح شیء، `workspace_id` اجباری، حذف `ModelViewSet` | تست‌های ادمین‌A/اپراتور‌B (list/retrieve/send/PATCH/DELETE ⇒ ۴۰۳/۴۰۴)، مدیر چندفروشگاهی بدون `workspace_id` ⇒ ۴۰۰ و با آن ⇒ مکالمهٔ درست؛ پر کردن `test_6` |
| **P0-2** | امن‌سازی پشتیبانی پلتفرم | SEC-03: `GenericViewSet`، نقش‌ها، `assign` ذخیره‌شونده، `reply` معتبر، idempotency | PATCH/DELETE ⇒ ۴۰۵/۴۰۳؛ `reply` بدون `client_message_id` ⇒ ۴۰۰؛ تکراری ⇒ ۴۰۹؛ `assigned_to` ذخیره شود |
| **P0-3** | مکالمهٔ مشتری | SEC-04: حذف destroy/create، اعتبارسنجی `team/queue` | اپراتور DELETE ⇒ ممنوع؛ FK فروشگاه دیگر ⇒ ۴۰۰ |
| **P0-4** | هویت مشتری | SEC-05: `external_id` فقط با ادعای امضاشده | جعل `external_id` ⇒ Visitor مجزا/۴۰۳؛ بازپخش `jti` ⇒ رد |
| **P1-1** | WS امن | SEC-06: تیکت WS، `is_active`، عضویت مجدد | تیکت منقضی/استفاده‌شده/مکالمهٔ دیگر ⇒ رد؛ کاربر غیرفعال ⇒ رد |
| **P1-2** | آغاز مکالمه توسط پلتفرم | `platform/support/start/`، UI در `platform-dashboard` (انتخاب فروشگاه + ایجاد)، اعلان به ادمین فروشگاه | مالک → فروشگاه X ⇒ ادمین X می‌بیند و پاسخ می‌دهد؛ فروشگاه Y نمی‌بیند؛ idempotent |
| **P1-3** | تکمیل پشتیبانی | بستن/بازگشایی، رسید، صفحه‌بندی، اعلان پیام جدید، پیوست، رابط مدیریت وضعیت | تست‌های ۲۲–۲۷ واقعی |
| **P1-4** | Seed امن | گارد `ENVIRONMENT`، حذف `is_staff` | اجرای seed در staging/production ⇒ خطا |
| **P1-5** | Provisioning و `allowed_domains` پویا | upsert Workspace/Project؛ نشست مشتری با انقضا | idempotency؛ origin نامعتبر ⇒ رد |
| **P2** | UX/موبایل/RTL، مدیا امضاشده، اعلان‌های بیرونی (SMS/ایمیل از `NotificationOutbox`)، آداپتور سفارش/کاتالوگ (حداقل PII)، E2E Playwright سه مسیر | مطابق چک‌لیست‌ها | مرورگر واقعی (دسکتاپ/موبایل/RTL) |
| **Phase C** (راستی‌سی) | مسیر ۱: ویجت در Storefront (با ادعای مشتری)؛ مسیر ۲: «پشتیبانی» در `/admin-portal/` (فروشگاه از Host)؛ مسیر ۳: بخش در `platformadmins` (Platform Admin) | پس از تأیید B |

> تغییرات راستی‌سی در **شاخهٔ مجزا و PR پیشنهادی** و فقط پس از تأیید شما و روشن شدن ریموت مرجع (بند ۱.۲).

---

## ۱۱. استقرار تدریجی و بازگشت

1. همهٔ کار روی شاخه‌های مجزا؛ هیچ merge به `main` بدون بازبینی.
2. **Feature flag** سمت راستی‌سی (`CHAT_INTEGRATION_ENABLED`، پیش‌فرض خاموش) و سمت چت (`RASTISI_SSO_ENABLED`).
3. مراحل: (۱) staging ایزوله با داده‌ٔ ساختگی ← (۲) مسیر ۳ برای یک فروشگاه آزمایشی داخلی ← (۳) مسیر ۲ ← (۴) مسیر ۱ برای یک فروشگاه ← (۵) گسترش.
4. **بازگشت:** خاموش‌کردن flag (UI راستی‌سی مخفی می‌شود؛ چت مستقل سالم می‌ماند)؛ Migrationهای چت افزایشی و معکوس‌پذیر؛ نسخهٔ قبلی چت با اسکریپت `rollback.sh` موجود (`scripts/staging/`) — **بدون اجرا در این مرحله**.
5. هیچ Workflow استقرار اجرا نشد و `staging-deploy.yml` (دستی) فعال نشد.

---

## ۱۱.۱ تصمیم‌های تأییدشده توسط مالک (پس از بازبینی گزارش)

| # | تصمیم | تأثیر بر طراحی |
|---|---|---|
| ۱ | هر چهار P0 (P0-1..P0-4) تأیید؛ هر کدام شاخه و PR مستقل با تست‌های ثبت‌شده در مخزن | ترتیب: دسترسی بین‌فروشگاهی → مجوز پشتیبانی پلتفرم → عملیات ناخواستهٔ اپراتور → `external_id` |
| ۲ | معماری گزینهٔ A (چت مستقل + SSO امضاشده + Provisioning سرور‌به‌سرور) | توکن: اعتبارسنجی امضا، `aud`، `exp`، ضد‌بازپخش (`jti`)، کنترل مجوز، نگاشت قطعی فروشگاه؛ هیچ شناسهٔ مرورگری به‌تنهایی مدرک نیست. **اجازهٔ ادغام DB/تغییر سرویس زنده/استقرار نیست.** |
| ۳ | ریموت مرجع: `Rastisi6-14040616` | بند ۱.۲ |
| ۴ | **نسخهٔ اول: آغاز مکالمه از پلتفرم فقط توسط Superuser معتبر راستی‌سی (`is_staff and is_superuser`)** | نقش «پشتیبانی پلتفرم» مستقل **در معماری پیش‌بینی** می‌شود (claim `role` و نگاشت به `PlatformMembership`) ولی **ساخته نمی‌شود** و مدل کاربران راستی‌سی تغییر نمی‌کند. |
| ۵ | Feature Flag پیش‌فرض **خاموش**؛ فعال‌سازی تدریجی برای فروشگاه آزمایشی سپس بقیه | دو سطح: Flag سراسری + Flag به‌ازای فروشگاه (§۱۱.۲) |
| ۶ | رفع دو مشکل CI در PRهای کوچک و جدا | بیت اجرایی اسکریپت‌ها؛ ارتقای وابستگی‌ها |
| ۷ | عدم تغییر VPS/DB/Nginx/ریموت/نسخهٔ فعال؛ عدم اجرای Workflow استقرار؛ عدم Merge بدون تأیید | رعایت می‌شود |

### ۱۱.۲ Feature Flag (طراحی)

- **سمت راستی‌سی (آینده، Phase C):** `CHAT_INTEGRATION_ENABLED` (env، پیش‌فرض `False`) **و** یک فیلد/تنظیم به‌ازای فروشگاه (`chat_enabled`، پیش‌فرض `False`، با Migration افزایشی). چت فقط وقتی هر دو روشن باشند نمایش داده می‌شود. انتشار نسخهٔ جدید هیچ فروشگاهی را روشن نمی‌کند؛ فعال‌سازی فقط با اقدام صریح مالک پلتفرم برای یک فروشگاه آزمایشی، سپس فهرست‌شده برای بقیه.
- **سمت چت:** `RASTISI_SSO_ENABLED` (پیش‌فرض `False`) و `Workspace` تنها وقتی Provisioning شده باشد برای SSO پذیرفته می‌شود؛ `/sso/exchange/` با Flag خاموش ۴۰۴ می‌دهد.
- بازگشت: خاموش‌کردن Flag بدون استقرار مجدد.

### ۱۱.۳ سازگاری و انتقال `external_id` (P0-4)

- **خطر:** غیرفعال کردن ناگهانی `external_id` عمومی، مشتریانی را که امروز با آن شناسایی می‌شوند و مکالمهٔ باز دارند قطع می‌کند؛ و مهمان‌ها (بدون `external_id`) نباید متأثر شوند.
- **وضعیت امروز:** ویجت رسمی `external_id` نمی‌فرستد؛ فقط مصرف‌کنندگان مستقیم API ممکن است بفرستند.
- **راهکار (دو فاز):**
  1. **فاز سازگار (همین PR):** مهمان بدون `external_id` دقیقاً مثل قبل (Visitor مجزا). درخواست عمومی **با** `external_id` بدون امضا: Visitor **جدید و مجزا** ساخته می‌شود (هرگز Visitor شناخته‌شدهٔ موجود برگردانده نمی‌شود) و مقدار به‌صورت `unverified_external_id` در `metadata` ثبت می‌شود. پس هیچ‌کس مکالمهٔ دیگری را نمی‌بیند. مشتریان با مکالمهٔ باز: Visitor/`session_token` قبلی آن‌ها همچنان معتبر است (نشست موجود از طریق `session_token` ادامه می‌یابد؛ فقط مسیر «تطبیق با external_id» بسته می‌شود).
  2. **فاز امضا (با SSO):** `external_id` فقط با ادعای امضاشده (`identity_verified=True`) پذیرفته و Visitor دامنه‌دار `store:customer` ساخته/ادغام می‌شود؛ ادغام مهمان→مشتری به‌صورت صریح و idempotent.
- **Flag سازگاری** `WIDGET_LEGACY_EXTERNAL_ID` (پیش‌فرض: رفتار امن). هیچ داده‌ای حذف یا بازنویسی نمی‌شود.

## ۱۲. وضعیت پس از بازبینی مالک و پرسش‌های باقی‌مانده

**Phase B با شرایط بند ۱۱.۱ آغاز شد.** پرسش‌های باز (نیاز به تأیید جداگانه):

1. ارتقای Django به 5.2 LTS (جهش از 4.2) و DRF به 3.17.2 — با آزمون کامل؛ شروع پس از P0ها.
2. تصمیم جدا و صریح دربارهٔ رساندن سرور راستی‌سی به `main` فعلی (§۱.۲.۱) — فقط مالک.
3. اصلاح عبارت `rastisi5` در `CLAUDE.md` راستی‌سی (PR جدا در راستی‌سی، پس از تأیید).
4. هر نقطهٔ ناشناخته: رفتار واقعی سرور/تولید، لاگ‌های Nginx، نسخهٔ مستقر چت، تست‌های راستی‌سی — از این محیط قابل بررسی نیست.

---

## ۱۳. بررسی یکپارچهٔ اصلاحات (Phase B، مرحلهٔ تأیید ترکیب)

شاخهٔ آزمایشی `claude/integration-p0-deps-verify` (PR #9، Draft، **Merge نشود**) از `main` ساخته شد و به ترتیب #3، #8، #4، #7، #5، #6 در آن ادغام شد.

### ۱۳.۱ Conflict و حل آگاهانه
- تنها Conflict: **خط import در `backend/conversations/views.py`** (تغییر مجاور در #4 با #7 و #5). حل: نگه‌داشتن `mixins` (#7/#5) و `transaction` (#4). pyflakes تأیید کرد نام تعریف‌نشده/گم‌شده نیست و هر سه ViewSet، `partial_update`، `resolve_admin_workspace` و پرچم `WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID` در ترکیب وجود دارند.
- برای حذف Conflict از خود PRها، import `transaction` در #4 به خط جدا (دور از import مجاور) منتقل شد. **اکنون هر شش PR در سه ترتیب آزموده‌شده (ترتیب پیشنهادی و دو ترتیب معکوس/مخلوط) بدون Conflict ادغام می‌شوند.**
- #8 اکنون commit اصلاح بیت اجرایی (#3) را هم دارد؛ شاخه‌های #4 تا #7 و این PR نیز #8 را (با merge commit، بدون rebase/force-push) دریافت کردند تا CI آن‌ها روی پایهٔ سالم اجرا شود.

### ۱۳.۲ نتایج راستی‌آزمایی نسخهٔ ترکیبی (Django 5.2.17 / DRF 3.17.2)
| بررسی | نتیجه |
|---|---|
| تست بک‌اند (۴ اجرای کامل **هم‌زمان** روی یک Redis) | **۵۵۸ تست، هر چهار OK**، صفر خطای Event Loop |
| `pip-audit`، `pip check` | بدون آسیب‌پذیری/تعارض |
| `makemigrations --check`، `check`، `check --deploy --fail-level WARNING --tag security` | موفق، بدون Migration جدید |
| Bandit `-ll` | بدون یافته |
| ویجت / operator-dashboard / platform-dashboard | typecheck OK؛ ۲۳ / ۹۴ / ۱۹ تست OK |
| GitHub Actions روی PR #9 | هر ۶ Job سبز (backend، docker-build، widget، دو داشبورد، secret-scan) |

### ۱۳.۳ خطای Event Loop در تست‌های WebSocket — تحلیل
- **مشاهده:** یک بار در اجرای کامل روی شاخهٔ #7 (پشتهٔ قدیمی Django 4.2/channels 4.0.0/channels-redis 4.1.0)، هم‌زمان با ۳ اجرای کامل دیگر: ابتدا `TimeoutError` در `receive_json_from` (تست `test_e2e_customer_chat_flow`)، سپس ۴ خطای `Lock … bound to a different event loop` در تست‌های بعدی.
- **قرائن:** اولین خطا Timeout است و بقیه ردیفی (cascade) بعد از آن. اجرای مجدد همان شاخه و همچنین ۴ اجرای کامل هم‌زمان روی پشتهٔ جدید، بدون خطا بود.
- **تلاش‌های بازتولید (همه ناموفق):** (۱) ۱۲ پردازش busy-loop هم‌زمان با تست‌های WS (۶۵ تست OK)؛ (۲) ۴ اجرای هم‌زمان زیرمجموعهٔ WS روی هر دو پشته (۸ اجرا OK)؛ (۳) تست مصنوعی «Timeout بدون disconnect و بعد تست WS دیگر» (۳ بار OK).
- **نتیجه:** علت قطعی **اثبات نشد**؛ نمی‌توان آن را صرفاً به «اجرای موازی» نسبت داد و تکرارپذیر هم نیست. فرضیهٔ محتمل: Timeout (۱–۲ ثانیه) در تست WS تحت فشار منابع، و باقی‌ماندن Task/اتصال کانال‌لایر ناتمام که تست‌های بعد با Event Loop جدید با آن برخورد می‌کنند. پشتهٔ جدید (channels-redis 4.3.0) در ۴ اجرای کامل و CI این خطا را نداد، اما این **اثبات نبود خطا نیست**. اگر در CI دیده شد: ثبت لاگ کامل، و آنگاه (الف) افزایش Timeoutها و (ب) پاک‌سازی کانال‌لایر در `tearDown` تست‌های WS.

### ۱۳.۴ پرچم `WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID`
پیش‌فرض خاموش (تست می‌شود). جلوگیری/شناسایی فعال‌شدن تصادفی در محیط عملیاتی (در PR #6):
1. staging/production با پرچم روشن **بدون** `WIDGET_UNVERIFIED_EXTERNAL_ID_ACK=accept-spoofable-customer-identity` **اصلاً بالا نمی‌آید** (`ImproperlyConfigured`).
2. با ack، سرور بالا می‌آید ولی `check --deploy --fail-level WARNING --tag security` (گیت CI/استقرار) با `visitors.W001` **قرمز** می‌شود.
3. هر استفاده یک WARNING لاگ می‌کند.
محدودیت: اگر کسی هر دو متغیر را عمداً بگذارد و گیت امنیتی را دور بزند، آسیب‌پذیری فعال است؛ و هشدار لاگ هنوز مانیتور نمی‌شود (مانیتورینگ/هشدار خودکار ساخته نشده).

### ۱۳.۵ محدودیت‌های باقی‌مانده (آزمایش‌شده روی نسخهٔ ترکیبی، اصلاح‌نشده)
| مورد | شواهد | شدت |
|---|---|---|
| WS داشبورد با JWT کاربر **غیرفعال** وصل می‌شود (REST همان توکن ⇒ ۴۰۱) | probe: `inactive-user WS connected: True`، REST ۴۰۱ | P1 |
| نشست مشتری **منقضی‌شده** پذیرفته می‌شود (`expires_at` هرگز بررسی نمی‌شود) | probe: `expired visitor session accepted: 200` | P1 |
| JWT (۶۰ دقیقه) و `session_token` در **مسیر/Query URL** WebSocket و `GET messages` | `config/routing.py` | P1 |
| `VisitorSession.token` بدون چرخش/ابطال؛ در `localStorage` | کد | P1 |
| عضویت فقط **هنگام اتصال** بررسی می‌شود؛ حذف عضو بعد از اتصال، سوکت باز را قطع نمی‌کند | کد (حذف عضو قبل از اتصال ⇒ رد می‌شود: probe) | P1 |
| `allowed_domains` پروژه اعمال نمی‌شود؛ CORS سراسری | کد | P1 |
| WS پشتیبانی و REST پشتیبانی نرخ‌محدودیت ندارند | کد | P2 |
| `/media/` بدون احراز | `deploy/nginx` | P2 |
