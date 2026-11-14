# Crib Link

**Find a home. View it with confidence.** A managed rental-viewing service in Kampala (pilot:
Ntinda). Crib Link staff inspect and publish rental homes; customers request a viewing, pay a
viewing fee once the time is confirmed, and a Crib Link broker goes to the viewing with them.

- How it works and why: [ARCHITECTURE.md](ARCHITECTURE.md)
- Business source: `Crib_Link_Complete_Business_Marketing_and_Lean_Operations_Plan.pdf` (13 Nov 2026)

## Run locally (Windows, no Docker needed)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py runserver
```

Customers and brokers sign in at http://127.0.0.1:8000/accounts/sign-in/ with a Ugandan mobile
number (e.g. `0772123456`). The 6-digit code appears in the **runserver console**, because dev
prints SMS instead of sending it.

Staff sign in with a password through the admin login, then work at http://127.0.0.1:8000/ops/.

```powershell
.venv\Scripts\python manage.py createsuperuser
```

A superuser holds every role, but still can't approve a listing they sourced or inspected. To
try the full flow, create staff users in the admin, tick **Staff status**, and put them in the
role groups (`acquisition`, `verification`, `listing_approval`, `bookings`, `finance`):

1. **Acquisition**: `/ops/listings/` → new contact → new property → add photos.
2. **Verification**: open the listing → record the inspection checklist.
3. **Listing approval** (a different person): approve. The home appears at `/homes/`.
4. **Bookings**: `/ops/brokers/` → onboard a broker.
5. **Customer** (phone sign-in): open the home → request a viewing.
6. **Bookings**: `/ops/bookings/` → confirm a time and a broker.
7. **Finance**: `/ops/finance/` → record the mobile-money transaction for the booking
   reference. Alternatively, set `PAYMENT_PROVIDER=dev` in `.env` and pay through the
   simulated checkout.
8. **Broker** (phone sign-in): `/broker/` → check in → file the report.

## Checks (same as CI)

```powershell
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format --check .
.venv\Scripts\python -m pytest            # fails under 80% coverage
.venv\Scripts\python manage.py makemigrations --check --dry-run
.venv\Scripts\pip-audit -r requirements.txt
```

Locally the tests run on SQLite with an in-memory cache. CI runs them on PostgreSQL and Redis,
which also exercises the audit-log database trigger and the row locks.
