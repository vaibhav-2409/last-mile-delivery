# Last-Mile Delivery Tracker

A delivery management platform where customers and admins create orders with
auto-calculated charges, agents are assigned intelligently, and customers are
notified at every step of the delivery journey.

**Stack:** FastAPI · SQLAlchemy 2.0 · SQLite (dev) / Postgres (prod) · vanilla JS
client, no build step. One process serves the API and the UI.

## Deliverables

All requested deliverables for this assignment are included in the repository:
1. **Zip file with complete source code**: [`last-mile-delivery-source.zip`](last-mile-delivery-source.zip) is included in the root of the repository.
2. **README with setup guide, .env.example, API docs, DB schema, and rate calculation logic explanation**: The setup guide and `.env.example` (located at `backend/.env.example`) explanation are in this README below. The docs are located in the `docs/` folder: [API docs](docs/API.md), [DB schema](docs/DB_SCHEMA.md), and [Rate calculation logic explanation](docs/RATE_LOGIC.md).
3. **Hosted application URL**: [https://last-mile-delivery-production-f275.up.railway.app](https://last-mile-delivery-production-f275.up.railway.app) (API docs available at [`/docs`](https://last-mile-delivery-production-f275.up.railway.app/docs)).
4. **System design write-up**: Located at [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) (Covers rate calculation engine, zone detection approach, auto-assignment logic, and failed delivery handling within the 800-word limit).

---

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # runs fine unedited
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. On first boot the app creates its tables and seeds
4 zones, 12 pincodes, 4 rate cards, COD rules, 4 agents and 2 customers.

### Demo logins

| Role | Email | Password |
|---|---|---|
| Admin | `admin@lastmile.dev` | `Admin@123` |
| Customer | `rohit@example.com` | `Passw0rd!` |
| Customer | `anitha@example.com` | `Passw0rd!` |
| Agent (Chennai North) | `arun.agent@lastmile.dev` | `Passw0rd!` |
| Agent (Chennai South) | `divya.agent@lastmile.dev` | `Passw0rd!` |
| Agent (Chennai West) | `karthik.agent@lastmile.dev` | `Passw0rd!` |
| Agent (Outstation) | `meera.agent@lastmile.dev` | `Passw0rd!` |

The sign-in screen has one-tap chips for these. **Change the admin password
before deploying anywhere public.**

### Tests

```bash
cd backend && pytest -q        # 30 tests: rate engine, assignment, lifecycle
```

---

## Five-minute walkthrough

1. **Sign in as the customer.** Book a delivery: 600001 → 600020, 40×30×20 cm,
   2 kg, B2C, cash on delivery. The quote panel prices it live — 4.8 kg
   volumetric beats 2 kg actual, so you're billed on 5 kg, ₹351.94 total. Nothing
   is booked until you press confirm.
2. **Sign in as the admin.** Open the order, press *Auto-assign nearest* and
   watch the tracking note record why that agent won ("in pickup zone, 4.3 km
   away, load 1/5").
3. **Sign in as that agent.** Walk the parcel through picked up → in transit →
   out for delivery, then mark the attempt failed with a reason.
4. **Back as the customer.** The order now offers a reschedule. Pick a date — the
   order moves to rescheduled and a *different* agent is assigned for attempt 2.
5. **Back as the admin.** *Verify tracking chain* recomputes every checkpoint's
   SHA-256 and confirms nothing has been altered. The notifications screen shows
   every email and SMS the flow produced.

---

## What's implemented

**Rate engine** — zone detection from pincodes, volumetric weight
(`L×B×H ÷ divisor`), billing on the higher of actual vs volumetric, separate
B2B/B2C rate cards for intra- and inter-zone movement, lane-specific overrides,
minimum charges, fuel surcharge, and per-order-type COD surcharge. Every input is
a database row an admin can edit — no pricing constants in code. Full detail in
[`docs/RATE_LOGIC.md`](docs/RATE_LOGIC.md).

**Auto-assignment** — ranks available agents with spare capacity by zone match,
then straight-line distance from their last known position to the pickup, then
current load. Reassignment after a failure excludes the agent who failed.

**Order lifecycle** — an explicit transition map, with agents restricted to the
five field statuses and admins able to override anything (logged as an override).

**Immutable tracking** — every status change writes a checkpoint with timestamp
and actor. Updates and deletes raise at the ORM layer, and each event carries a
SHA-256 chained to the previous one, so tampering via direct database access is
detectable. An admin endpoint verifies the chain.

**Failed delivery flow** — reason captured, attempt counter incremented, customer
notified, new date collected, agent reassigned.

**Notifications** — email on every status change, SMS when a phone number is on
file. Both are persisted to the database first and sent on a background task, so
comms never block the API. Without provider credentials they're recorded as
`SIMULATED` and logged, which keeps the whole flow demonstrable on a free tier.

**Roles** — customer, delivery agent, admin, on JWT auth with per-route
enforcement.

---

## Configuration

Copy `.env.example` to `.env`. It works unedited for local development.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./lastmile.db` | `postgres://` URLs are rewritten for SQLAlchemy 2.x automatically |
| `JWT_SECRET` | `change-me-in-production` | **set this** in any deployment |
| `ACCESS_TOKEN_TTL_MINUTES` | `720` | |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@lastmile.dev` / `Admin@123` | bootstrap admin, created on first boot |
| `AUTO_SEED_ON_BOOT` | `true` | idempotent; set `false` once you have real data |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | used for tracking links in emails |
| `NOTIFICATIONS_ENABLED` | `true` | |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | empty | unset ⇒ emails are simulated and logged |
| `MAIL_FROM` / `MAIL_FROM_NAME` | | sender identity |
| `SMS_PROVIDER` | empty | `twilio` or `fast2sms`; unset ⇒ SMS simulated |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | | Twilio trial credentials |
| `FAST2SMS_API_KEY` | | Fast2SMS free tier |
| `CORS_ORIGINS` | `*` | comma-separated; narrow this if you split the frontend out |

### Turning on real notifications

Both are optional — the app is fully functional without them.

**Email (Brevo free tier, 300/day):** sign up, take an SMTP key, then set
`SMTP_HOST=smtp-relay.brevo.com`, `SMTP_PORT=587`, `SMTP_USER=<your login>`,
`SMTP_PASSWORD=<smtp key>`, `MAIL_FROM=<verified sender>`. Gmail works too with an
app password (`smtp.gmail.com`). Mailtrap is good for testing without real
delivery.

**SMS (Twilio trial):** `SMS_PROVIDER=twilio` plus the three Twilio variables.
Trial accounts only send to verified numbers. For India, Fast2SMS is simpler:
`SMS_PROVIDER=fast2sms` and `FAST2SMS_API_KEY`.

Check `/api/health` or the admin Notifications screen to confirm which providers
are live versus simulated.

---

## Deployment

> **Note:** This app uses FastAPI to serve both the API and static frontend from a **single persistent process**. Use **Render**, **Railway**, or **Fly.io** — not Vercel/Netlify, which are serverless-only and do not support long-running Python servers with file-system access.

### Render (recommended — `render.yaml` is included)

1. Push this repository to GitHub (already done — see https://github.com/vaibhav-2409/last-mile-delivery).
2. Go to [https://dashboard.render.com](https://dashboard.render.com) → **New → Blueprint**.
3. Connect your GitHub account and select the `last-mile-delivery` repo.
4. Render will read `render.yaml` and automatically provision:
   - A free **Postgres** database (`lastmile-db`)
   - A **Web Service** running `uvicorn app.main:app`
5. In the dashboard, set `ADMIN_PASSWORD` and `PUBLIC_BASE_URL` (your Render URL).
6. Click **Apply**. First boot creates the schema and seeds the demo data.

Two Render notes worth knowing: pin Python with `runtime.txt` (included —
otherwise Render may default to a version some wheels don't build on), and free
instances sleep after inactivity, so the first request wakes them. A five-minute
UptimeRobot ping on `/api/health` keeps it warm.

### Railway / Fly / anything else

```bash
pip install -r backend/requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port $PORT     # run from backend/
```

Set `DATABASE_URL` to your Postgres URL and `JWT_SECRET` to something random.

### Docker

```bash
docker build -t lastmile . && docker run -p 8000:8000 --env-file backend/.env lastmile
```

---

## Project layout

```
backend/
  app/
    main.py            FastAPI app, exception handlers, static mount
    config.py          environment configuration
    database.py        engine + session factory
    models.py          all ten tables, plus the append-only guards
    schemas.py         pydantic request/response contracts
    security.py        PBKDF2 hashing, JWT issue/verify
    deps.py            auth dependencies, role guards
    seed.py            idempotent demo data
    services/
      rate_engine.py   quote pipeline — weight, card lookup, COD
      zones.py         pincode → zone detection, haversine
      assignment.py    agent ranking and capacity accounting
      lifecycle.py     transition map, event recording, chain verification
      notifications.py email/SMS templates and transport
    routers/
      auth.py  public.py  orders.py  agent.py  admin.py
  tests/test_engine.py 30 unit tests
frontend/
  index.html  app.js  styles.css
docs/
  SYSTEM_DESIGN.md   architecture write-up (800 words)
  RATE_LOGIC.md      rate calculation, with worked examples
  API.md             endpoint reference
  DB_SCHEMA.md       tables, relationships, indexes
render.yaml  Dockerfile  runtime.txt
```

## Documentation

- [System design write-up](docs/SYSTEM_DESIGN.md) — rate engine, zone detection,
  auto-assignment, failed-delivery handling
- [Rate calculation logic](docs/RATE_LOGIC.md) — the pipeline plus worked examples
- [API reference](docs/API.md) — every endpoint, roles, payloads
- [Database schema](docs/DB_SCHEMA.md) — tables, relationships, indexes

## Notes on scope

`Base.metadata.create_all()` handles schema creation, which suits a project this
size; `docs/DB_SCHEMA.md` sketches the Alembic path for a real rollout. Auto-
assignment uses straight-line distance rather than road distance — swapping in a
routing API means replacing one function in `services/assignment.py`, since the
ranking already treats distance as a pluggable input.
