# Crib Link: System Architecture

**Source of requirements:** `Crib_Link_Complete_Business_Marketing_and_Lean_Operations_Plan.pdf`
(13 Nov 2026). It replaces `Crib_Link_Product_and_Launch_Plan.pdf` (Oct 2026) and
`rental_platform_strategy.pdf` (Aug 2026) wherever they disagree. The main changes:

- Crib Link is a **managed viewing service**, not a classifieds site. Staff source and verify
  listings; landlords don't self-list (owner/partner submissions may come later).
- Customers pay a **viewing fee** (about US$12, working assumption). v1 now moves money.
- A Crib Link **broker** goes to every viewing with the customer and files a report.
- No contact reveal, no tenant–landlord messaging: the platform coordinates the viewing.

> Core promise: *Crib Link doesn't just help people find houses. It takes responsibility for
> helping them view verified houses.*

## 1. The service, end to end

| # | Stage | Who | Where in the code |
|---|---|---|---|
| 1 | **Source**: owner/caretaker/agent contact, written permission to advertise | Acquisition | `listings.services.create_contact`, `create_property` |
| 2 | **Verify**: on-site inspection against a fixed checklist, with photos | Verification | `listings.services.add_photo`, `submit_verification` |
| 3 | **Approve & publish**: by someone who neither sourced nor inspected it | Listing approval | `listings.services.decide_verification` |
| 4 | **Discover**: browse, filter by area / rent / bedrooms / type | Customer | `/homes/`, `listings.selectors.search` |
| 5 | **Request**: preferred time; *nothing to pay yet* | Customer | `bookings.services.request_viewing` |
| 6 | **Confirm**: property access + broker availability for a slot | Bookings | `bookings.services.confirm` |
| 7 | **Pay**: fee fixed at confirmation; confirmed server-side only | Customer + Finance | `payments.services` |
| 8 | **Assign**: broker gets customer, directions and contact *once paid* | System | `bookings.services.mark_paid` |
| 9 | **View**: broker checks in on arrival | Broker | `bookings.services.check_in` |
| 10 | **Report & follow up**: attendance, did it match the listing, customer interest; feedback | Broker, Customer | `submit_report`, `leave_feedback` |

**Payment order (plan section 2):** request → confirm → pay. A confirmed slot stays held until
`PAYMENT_WINDOW_HOURS` or `PAYMENT_CLOSE_BEFORE_START_HOURS` before the start, whichever comes
first; after that it lapses. **A browser redirect never marks a payment as paid.** Only these two
paths do:

- **`manual`** (pilot default): the customer pays the business mobile-money number quoting the
  booking reference; finance records the provider transaction ID from the merchant statement.
- **Signed webhook** (`SignedWebhookProvider`): HMAC-SHA256 over `timestamp.body`, a 5-minute
  tolerance, replays dropped by event ID, amount and currency checked. `dev` simulates this
  locally; production refuses to start with it. A real provider (MTN MoMo, Airtel Money or an
  aggregator) is a subclass.

### State machines

```
Property     DRAFT ──inspect──▶ AWAITING_APPROVAL ──approve (other person)──▶ PUBLISHED
               ▲                   │ send back                                  │  │
               └───────────────────┘                     recheck date passes ◀──┘  ├─▶ PAUSED
             edits always return a listing to DRAFT        └─▶ EXPIRED (re-inspect)  └─▶ RENTED

Appointment  REQUESTED ──confirm──▶ AWAITING_PAYMENT ──paid──▶ BOOKED ──check-in + report──▶ COMPLETED
                │ decline               │ window passes          │                         CUSTOMER_NO_SHOW
                ▼                       ▼                        ▼ cancel (refund per rules)
             DECLINED                LAPSED                   CANCELLED
```

Only `PUBLISHED` listings inside their recheck window (`Property.objects.bookable()`) appear to
customers. A recheck of a live listing keeps it live while the recheck is reviewed.

## 2. Shape: a modular monolith

One Django project, one database, one deploy. Each domain lives in its own app with its own
models, services and tests. It stays a monolith because there is one small team and one pilot
area (Ntinda), and because the trust-critical steps (payment → booking → broker notice → audit)
must commit in a single database transaction. The plan says to build only what operations
need; app boundaries are enforced in code, so one app can be extracted later if it ever needs
to be.

```
            Browser (mobile-first server-rendered HTML)
                          │ HTTPS
                ┌─────────▼──────────┐       ◀── signed payment webhooks
                │  Django (gunicorn)  │  views → services → models
                └──┬──────┬───────┬───┘
         PostgreSQL│ Redis│cache  │ Redis broker
                   │      │+rate  ▼
                   │      │limits Celery: SMS (Africa's Talking), listing expiry,
                   │      │       unpaid-slot lapse, reminders, selfie purge
         S3 / R2: public bucket (listing photos)
                  private bucket (ID scans, written permission to advertise)
```

## 3. Apps

| App | Owns |
|---|---|
| `core` | Base models, phone normalisation, rate limiter, SMS gateway, image sanitising, **staff roles**, health check |
| `accounts` | `User` (phone is the login), SMS one-time codes, sign-in and sign-out |
| `audit` | `AuditEvent`, append-only (enforced in the ORM and by a Postgres trigger) |
| `landlords` | Owner identity check (`LandlordProfile`, `IdentitySubmission`, NIN as an HMAC), super-admin review at `/review/identity/`. A verified owner can be linked to a `PropertyContact` as extra proof of authority. |
| `moderation` | `BlockedIdentity` (fraud bans) |
| `listings` | `PropertyContact`, `Property`, `PropertyPhoto`, `Verification` (checklist, maker-checker), expiry, public search at `/homes/` |
| `brokers` | `BrokerProfile` (ID checked, written terms signed, areas, active/suspended) |
| `bookings` | `Appointment`, `ViewingReport`, `Feedback`; customer pages at `/viewings/`, broker pages at `/broker/` |
| `payments` | `Payment` (one row per attempt), `Refund`, `WebhookEvent`; providers; webhook at `/payments/webhook/` |
| `ops` | Staff screens at `/ops/`: weekly dashboard, listings pipeline, booking desk, brokers, finance |

**Appointment and payment records are separate** (plan section 6), so retries, failures,
duplicate payments and refunds never rewrite the appointment's history.

### Roles

Staff hold roles as Django groups (`core.roles.Role`), created automatically on `migrate`.
Staff sign in with a password; SMS sign-in is refused for staff because of SIM-swap risk.

| Role | Can |
|---|---|
| `acquisition` | add contacts and listings, edit off-air listings, add photos |
| `verification` | add photos, record inspections, view the written permission |
| `listing_approval` | approve or send back inspections, view the written permission |
| `bookings` | confirm, decline, reschedule and cancel viewings; onboard and suspend brokers |
| `finance` | record mobile-money payments, pay out refunds, reconcile |

Customers and brokers are not staff. A `BrokerProfile` makes someone a broker; brokers sign in
by SMS code and see only the appointments assigned to them.

**Separation of duties (enforced in services and tests, and it applies to superusers too):**

- Whoever sourced or inspected a listing cannot approve it.
- Whoever requested a refund cannot mark it paid.
- Whoever recorded a manual payment cannot reconcile it.
- Staff accounts cannot also be brokers.
- Admin pages for these models are read-only, so every change goes through the validated,
  audited services.

## 4. Code rules (what keeps it testable)

- **Thin views → services → models.** Views parse input and render. Every write with business
  rules goes through `apps/<app>/services.py`, which also checks roles. Tests target services
  directly.
- **Apps talk through services.** `payments` calls `bookings.services.mark_paid`; `bookings`
  calls `payments.services.request_refund`. Neither writes the other's tables.
- **Every state change writes an `AuditEvent`** in the same transaction.
- **Side effects run after commit.** SMS is queued with `transaction.on_commit`.
- **Row locks on every transition** (`select_for_update`, `of=("self",)` where a nullable join
  is involved, because Postgres can't lock the nullable side of an outer join). A broker's row
  is locked while a slot is claimed, so two coordinators can't double-book them.
- **UUID primary keys** on every row in a URL; human-friendly references (`CL7K3Q9P`) for
  phone and SMS use.

## 5. Security controls

| Threat | Control |
|---|---|
| Account takeover by guessing codes | 6-digit code stored only as an HMAC, 5-minute expiry, 5 attempts, single use |
| SMS cost fraud / spam | Ugandan numbers only; per-phone cooldown and hourly caps; per-IP cap |
| Finding out who is registered | Sign-in and sign-up are the same flow, with the same response |
| Session theft or fixation | Session key rotated at sign-in; Secure/HttpOnly/SameSite cookies; HSTS; POST-only sign-out |
| XSS / clickjacking | Auto-escaping, CSP `'self'` only (no inline script), `X-Frame-Options: DENY` |
| CSRF | On every POST. The payment webhook is exempt and authenticated by HMAC instead |
| **Forged or replayed payment** | Signed webhook (HMAC, timestamp tolerance, constant-time compare); unique event IDs; amount and currency must match or the payment is held as `mismatch`; unique provider transaction IDs; redirects never mark payments paid |
| **Money for a released slot** | Booking only if still `AWAITING_PAYMENT`; otherwise an automatic refund request |
| **Insider fraud** | Separation of duties above; every action audited with actor and evidence |
| **Customer data reaching the wrong person** | Public listing pages never show directions or contact phones; a broker sees the customer's phone and directions only for *their own paid* viewings; customers see only their own appointments (404 otherwise) |
| **Request spam** | At most 3 open viewings per customer, 10 requests a day, one open viewing per home |
| Leaked ID or authority documents | Private storage, streamed through Django to authorised roles only (`no-store`), every view audited, selfies purged after 90 days |
| Malicious uploads | Every photo is decoded and re-encoded to JPEG (EXIF/GPS stripped), with size and pixel caps |
| Tampering with evidence | `AuditEvent` blocks updates and deletes in the ORM and with a database trigger |
| Admin exposure | Admin URL from env; staff must use a password; model admins are read-only |
| Misconfigured production | Production refuses to start with the dev payment provider, with manual payments but no merchant number, or with a webhook provider but no 32+ character secret; CI runs `check --deploy` |

## 6. Environments and settings

`config/settings/{base,dev,test,prod}.py`; secrets come from environment variables (see
`.env.example`).

| | dev | test / CI | prod |
|---|---|---|---|
| Database | SQLite | SQLite locally; **PostgreSQL in CI** | PostgreSQL |
| Cache / rate limits | in-memory | in-memory; **Redis in CI** | Redis |
| Celery | inline | inline | Redis broker + workers + beat |
| SMS | console | in-memory outbox | Africa's Talking |
| Payments | `manual` (or `dev` to simulate checkout) | `manual`, and `dev` in tests | `manual` or a real provider |
| Files | local disk | in-memory | S3/R2 (public + private buckets) |

**Operating assumptions (all in `base.py` and overridable by env; the plan's "decisions to
finalise"):**

| Setting | Default | Plan question |
|---|---|---|
| `VIEWING_FEE_UGX` | 44,000 (≈ US$12), **per appointment, one property** | Fee unit |
| Payment order | after confirmation | Payment timing |
| `PAYMENT_WINDOW_HOURS` / `PAYMENT_CLOSE_BEFORE_START_HOURS` | 12 / 2 | Payment timing |
| `CANCELLATION_REFUND_CUTOFF_HOURS` | 3: a customer who cancels earlier gets a full refund, later gets none; staff-side cancellations (property gone, broker no-show) are always refunded | Cancellation policy |
| `SERVICE_AREAS` | Ntinda, Kiwatule, Kigoowa, Naguru | Exact pilot boundary |
| `LISTING_RECHECK_DAYS` | 14 | Recheck rule |
| `APPOINTMENT_SLOT_MINUTES` | 60 (a broker can't hold two slots closer than this) | Appointment scope |
| Broker assignment | manual, by the coordinator | Broker assignment |
| Inventory | staff-sourced only | Inventory source |
| Broker pay | **not modelled yet** | Broker compensation |

**Geo decision:** locations are an area plus a free-text public hint and private directions.
Maps and PostGIS come later, behind `listings.selectors`.

## 7. Testing strategy

- **pytest + pytest-django.** Shared fixtures in the root `conftest.py` build each stage:
  staff with roles → published listing → broker → requested → awaiting payment → booked.
- **Service tests** cover every transition and every refusal: wrong role, wrong owner, the
  separation-of-duties rules, double booking, lapse, refund rules, webhook tampering and replay.
- **View tests** cover data exposure: anonymous users, other customers, other brokers, and
  broker visibility before and after payment.
- **Coverage gate:** 80% minimum (currently about 97%).
- **CI:** ruff → missing-migration check → pytest on PostgreSQL + Redis → `check --deploy` →
  `pip-audit`.

## 8. Build order

1. **Done:** foundation (settings, core, accounts, audit, CI) and the owner identity check.
2. **Done:** property operations: intake, inspection, approval, expiry, public search.
3. **Done:** bookings and payments: request → confirm → pay (manual + signed webhook), lapse,
   cancellation and refunds, reconciliation.
4. **Done:** broker workflow: assignment on payment, check-in, viewing report, flags.
5. **Done (basic):** weekly management dashboard, customer feedback, SMS reminders.
6. **Next, only when pilot data shows the need** (plan sections 5.3 and 6): a real payment
   provider integration, broker compensation records, WhatsApp Business templates, maps,
   multi-property outings, automated broker suggestions. **Deliberately not built:** AI
   recommendations, automatic dispatch, route optimisation.
