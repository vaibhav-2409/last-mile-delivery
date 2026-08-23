# API Reference

Base URL: `http://localhost:8000` locally, your host URL in production.
Interactive docs ship with the app at **`/docs`** (Swagger) and **`/redoc`**.

All authenticated routes take `Authorization: Bearer <token>`. Tokens come from
`/api/auth/login` or `/api/auth/register` and are valid for
`ACCESS_TOKEN_TTL_MINUTES` (default 12 hours).

Errors are `{"detail": "message"}` with conventional codes: `401` not signed in,
`403` wrong role, `404` missing, `409` illegal state change, `422` validation or
configuration problem (unserviceable pincode, missing rate card).

---

## Auth

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | Self-signup. Always creates a `CUSTOMER`. |
| POST | `/api/auth/login` | public | Returns a JWT plus the user record. |
| GET | `/api/auth/me` | any | Current user. |

```bash
curl -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@lastmile.dev","password":"Admin@123"}'
```

## Public

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Status plus which notification providers are live vs simulated. |
| GET | `/api/zones` | Active zones. |
| GET | `/api/serviceable` | Every serviceable pincode. |
| GET | `/api/zone-lookup/{pincode}` | Detected zone, or `404` if unserviceable. |

## Orders

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/orders/quote` | customer, admin | Price a shipment without booking it. |
| POST | `/api/orders` | customer, admin | Create an order. Admins pass `customer_id`. |
| GET | `/api/orders` | any | List, scoped to the caller's role. |
| GET | `/api/orders/{id}` | any | Full detail with tracking history. |
| GET | `/api/orders/code/{order_code}` | any | Look up by waybill number. |
| GET | `/api/orders/{id}/tracking` | any | Tracking checkpoints only. |
| POST | `/api/orders/{id}/reschedule` | customer | New date after a failed attempt; reassigns an agent. |
| POST | `/api/orders/{id}/cancel` | customer | Cancel before delivery. |
| POST | `/api/orders/{id}/assign` | admin | Manually assign an agent. |
| POST | `/api/orders/{id}/auto-assign` | admin | Assign the nearest available agent. |

`GET /api/orders` filters (admin only unless noted): `status`, `zone_id`,
`agent_id`, `customer_id`, `search` (order code or pincode), `limit`, `offset`.
Customers see only their own orders; agents see only orders assigned to them,
regardless of the filters passed.

### Quote

```bash
curl -X POST localhost:8000/api/orders/quote \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"pickup_pincode":"600001","drop_pincode":"600020",
       "length_cm":40,"breadth_cm":30,"height_cm":20,"actual_weight_kg":2,
       "order_type":"B2C","payment_type":"COD"}'
```

```json
{
  "pickup_zone": {"code": "CHN-N", "name": "Chennai North"},
  "drop_zone":   {"code": "CHN-S", "name": "Chennai South"},
  "scope": "INTER",
  "actual_weight_kg": 2.0,
  "volumetric_weight_kg": 4.8,
  "billable_weight_kg": 5.0,
  "weight_basis": "VOLUMETRIC",
  "volumetric_divisor": 5000.0,
  "rate_card_id": 2,
  "rate_card_name": "B2C Inter-zone standard",
  "freight_charge": 299.0,
  "fuel_surcharge": 17.94,
  "cod_surcharge": 35.0,
  "total_charge": 351.94,
  "lines": [
    {"label": "Freight", "detail": "B2C Inter-zone standard · 5 kg billable …", "amount": 299.0},
    {"label": "Fuel surcharge", "detail": "6% of freight", "amount": 17.94},
    {"label": "COD handling", "detail": "B2C COD: higher of ₹35 or 2%", "amount": 35.0}
  ]
}
```

### Create

Body is the quote fields plus addresses. Optional: `pickup_contact`,
`pickup_phone`, `pickup_lat`/`pickup_lng` (and the `drop_*` equivalents),
`package_description`, `scheduled_date`, `auto_assign`, `customer_id` (admin).
Charges are always recalculated server-side — anything the client sends about
price is ignored.

## Agent

| Method | Path | Description |
|---|---|---|
| GET | `/api/agent/me` | Own profile: zone, vehicle, capacity, position. |
| PATCH | `/api/agent/me` | Toggle availability, push a GPS fix. |
| GET | `/api/agent/orders` | Assigned orders (`?active_only=true`). |
| POST | `/api/agent/orders/{id}/status` | Field status update. |

Agents may set only `PICKED_UP`, `IN_TRANSIT`, `OUT_FOR_DELIVERY`, `DELIVERED`,
`FAILED`, and only on their own orders. `FAILED` requires a `failure_reason` or
`note`. Sending `lat`/`lng` also refreshes the agent's position.

## Admin

| Method | Path | Description |
|---|---|---|
| GET/POST | `/api/admin/zones` | List / create zones. |
| PATCH/DELETE | `/api/admin/zones/{id}` | Update; delete refuses if orders reference the zone. |
| GET/POST | `/api/admin/areas` | List / map a pincode to a zone (`?zone_id=` filter). |
| PATCH/DELETE | `/api/admin/areas/{id}` | Remap or unmap a pincode. |
| GET/POST | `/api/admin/rate-cards` | List / create rate cards. |
| PATCH | `/api/admin/rate-cards/{id}` | Edit pricing. |
| DELETE | `/api/admin/rate-cards/{id}` | Retires the card (`is_active=false`), never hard-deletes. |
| GET/PUT | `/api/admin/cod-rules` | COD surcharge per order type. |
| GET/PUT | `/api/admin/settings` | Engine knobs: `volumetric_divisor`, `billable_rounding_step`. |
| GET | `/api/admin/customers` | Customer directory (`?search=`). |
| GET/POST | `/api/admin/agents` | List / create agents (`?available_only=true`). |
| PATCH | `/api/admin/agents/{user_id}` | Zone, vehicle, capacity, availability, position. |
| POST | `/api/admin/orders/{id}/override-status` | Force any status; logged as an override. |
| GET | `/api/admin/orders/{id}/integrity` | Recompute the tracking hash chain. |
| GET | `/api/admin/notifications` | Notification log (`?order_id=`, `?limit=`). |
| GET | `/api/admin/stats` | Dashboard counters. |

### Integrity check

```json
{"order_code": "LM260823F602C7", "events": 8, "intact": true, "broken_at": null}
```

`intact: false` with a `broken_at` event id means a row was altered outside the
application — the ORM blocks edits, so this only fires on direct database access.

---

## Status lifecycle

```
CREATED ──► ASSIGNED ──► PICKED_UP ──► IN_TRANSIT ──► OUT_FOR_DELIVERY ──► DELIVERED
   │            │            │              │                  │
   │            └────────────┴──────────────┴──────────────────┴──► FAILED
   │                                                                  │
   └──► CANCELLED ◄───────────────────────────────────────────────────┤
                                                                      ▼
                                          RESCHEDULED ──► ASSIGNED (new agent)
```

Any transition outside this map returns `409`, except an admin override, which
is permitted and stamped `is_override: true` in the tracking history.
