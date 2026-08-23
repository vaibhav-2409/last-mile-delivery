# Database Schema

Ten tables. Everything the rate engine and dispatch logic reads is a row, not a
constant — zones, pincode mappings, rate cards, COD rules and the two engine
knobs are all editable at runtime by an admin.

```
                    ┌──────────────┐
                    │    users     │  role: CUSTOMER | AGENT | ADMIN
                    └──────┬───────┘
             ┌─────────────┼──────────────┐
             │             │              │
      customer_id   created_by_id     agent_id            ┌────────────────┐
             │             │              │          ┌────│ agent_profiles │
             ▼             ▼              ▼          │    └────────────────┘
        ┌─────────────────────────────────────┐      │      1:1 with a user
        │              orders                 │      │      of role AGENT
        └───┬──────────────┬──────────────┬───┘      │
            │              │              │          │
            ▼              ▼              ▼          │
  ┌──────────────────┐ ┌───────────┐ ┌──────────────────────┐
  │ tracking_events  │ │notificat- │ │ reschedule_requests  │
  │  (append-only)   │ │   ions    │ └──────────────────────┘
  └──────────────────┘ └───────────┘
                                          ┌─────────┐      ┌────────┐
        orders.pickup_zone_id ────────────│  zones  │◄─────│ areas  │
        orders.drop_zone_id   ────────────└────┬────┘      └────────┘
                                               │            pincode → zone
        rate_cards.from_zone_id / to_zone_id ──┘

  ┌────────────┐   ┌───────────┐   ┌──────────────────┐
  │ rate_cards │   │ cod_rules │   │ system_settings  │   pricing configuration
  └────────────┘   └───────────┘   └──────────────────┘
```

---

## users
Single identity table for all three roles.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(120) | |
| `email` | varchar(180) | unique, indexed |
| `phone` | varchar(20) | used for SMS |
| `password_hash` | varchar(255) | PBKDF2-HMAC-SHA256, 240k iterations, per-user salt |
| `role` | enum | `CUSTOMER` / `AGENT` / `ADMIN` |
| `is_active` | bool | deactivation blocks login |
| `created_at` | timestamptz | |

## agent_profiles
The availability and location model auto-assignment ranks on. One row per agent.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK users | unique — enforces 1:1 |
| `home_zone_id` | FK zones | nullable; first ranking criterion |
| `vehicle_type` | varchar(40) | `BIKE` / `VAN` / `TRUCK` |
| `is_available` | bool | on/off duty |
| `max_active_orders` | int | capacity ceiling |
| `active_orders` | int | current load, moved on attach/release |
| `current_lat`, `current_lng` | float | last GPS fix |
| `location_updated_at` | timestamptz | staleness signal |

## zones
| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `code` | varchar(20) | unique, e.g. `CHN-N` |
| `name` | varchar(120) | |
| `centroid_lat`, `centroid_lng` | float | distance fallback when no GPS is available |
| `is_active` | bool | inactive zones are skipped by detection |

## areas
Pincode → zone mapping. This table *is* the zone boundary definition.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `pincode` | varchar(12) | **unique**, indexed — one pincode, one zone |
| `name`, `city`, `state` | varchar | |
| `zone_id` | FK zones | cascade delete |
| `lat`, `lng` | float | |
| `is_serviceable` | bool | false ⇒ bookings to this pincode are refused |

## rate_cards
Slab pricing for one `(order_type, scope)` pair, optionally narrowed to a lane.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(120) | shown on the quote |
| `order_type` | enum | `B2B` / `B2C`, indexed |
| `scope` | enum | `INTRA` / `INTER`, indexed |
| `from_zone_id`, `to_zone_id` | FK zones | both null = default card; both set = lane override |
| `base_weight_kg` | float | weight included in `base_price` |
| `base_price` | float | |
| `increment_weight_kg` | float | slab size beyond the base |
| `increment_price` | float | price per slab |
| `min_charge` | float | freight floor |
| `fuel_surcharge_pct` | float | percentage applied to freight |
| `is_active` | bool | retired cards stay for historical orders |

Lookup precedence is `(from_zone, to_zone)` → `(from_zone, null)` → `(null, null)`,
computed as a `specificity` score at selection time.

## cod_rules
One row per order type. `UNIQUE(order_type)`.

| Column | Type | Notes |
|---|---|---|
| `flat_fee` | float | |
| `percent_of_freight` | float | |
| `min_fee` | float | floor |
| `max_fee` | float, nullable | cap |

Fee = `clamp(max(flat_fee, freight × percent/100), min_fee, max_fee)`.

## system_settings
Key/value engine knobs, so the weight maths has no literals in code.

| Key | Default | Meaning |
|---|---|---|
| `volumetric_divisor` | `5000` | divisor in `L×B×H ÷ divisor` |
| `billable_rounding_step` | `0.5` | billable weight rounds up to this step |

## orders
| Group | Columns |
|---|---|
| Identity | `id`, `order_code` (unique, `LM<yymmdd><hex>`), `customer_id`, `created_by_id`, `agent_id` |
| Pickup | `pickup_contact`, `pickup_phone`, `pickup_address`, `pickup_pincode`, `pickup_zone_id`, `pickup_lat`, `pickup_lng` |
| Drop | same shape, `drop_*` |
| Package | `length_cm`, `breadth_cm`, `height_cm`, `actual_weight_kg`, `volumetric_weight_kg`, `billable_weight_kg`, `weight_basis` (`ACTUAL`/`VOLUMETRIC`) |
| Commercials | `order_type`, `payment_type`, `rate_scope`, `rate_card_id`, `freight_charge`, `fuel_surcharge`, `cod_surcharge`, `total_charge`, `charge_breakdown` (JSON snapshot at booking time) |
| Lifecycle | `status`, `scheduled_date`, `delivery_attempts`, `failure_reason`, `created_at`, `updated_at`, `delivered_at` |

`created_by_id` is separate from `customer_id` so an admin booking on behalf of a
customer is visible in the record. `charge_breakdown` freezes the quote, so
editing a rate card later never changes what an existing order was sold for.

## tracking_events — append-only
| Column | Type | Notes |
|---|---|---|
| `id` | int PK | ordering is by id |
| `order_id` | FK orders | indexed, cascade |
| `status`, `previous_status` | enum | |
| `actor_id`, `actor_role`, `actor_name` | | who did it; name is denormalised so history survives a user rename |
| `note`, `location_text`, `lat`, `lng` | | |
| `is_override` | bool | admin forced this transition |
| `created_at` | timestamptz | |
| `prev_hash` | char(64) | previous event's `event_hash`, null on the first |
| `event_hash` | char(64) | SHA-256 over order, status, actor, note, timestamp, `prev_hash` |

Two independent guarantees: SQLAlchemy `before_update` / `before_delete` listeners
raise `ImmutableRecordError`, and the hash chain detects edits made directly in
the database. `GET /api/admin/orders/{id}/integrity` recomputes the chain.

## reschedule_requests
| Column | Notes |
|---|---|
| `order_id`, `requested_date`, `reason`, `requested_by_id` | |
| `previous_agent_id`, `new_agent_id` | who failed the attempt, who picked it up |
| `attempt_number` | which attempt this reschedule is for |

## notifications
Every email and SMS, written whether or not a provider is configured.

| Column | Notes |
|---|---|
| `order_id`, `channel` (`EMAIL`/`SMS`), `recipient`, `subject`, `body` | |
| `status` | `SENT` / `SIMULATED` / `FAILED` |
| `provider_response` | truncated provider reply or the failure reason |
| `trigger_status` | which order status produced it |

---

## Indexes

Beyond the primary keys: unique on `users.email`, `areas.pincode`,
`orders.order_code`, `cod_rules.order_type`, `agent_profiles.user_id`; plain
indexes on `orders.status`, `orders.customer_id`, `orders.agent_id`,
`orders.created_at`, both zone columns on orders, `tracking_events.order_id`,
`notifications.order_id`, and `rate_cards.order_type` / `.scope` — the two
columns every rate lookup filters on.

## Migrations

Tables are created with `Base.metadata.create_all()` at startup, which is
appropriate for this scope. For a production rollout, add Alembic
(`alembic init`, point `sqlalchemy.url` at `DATABASE_URL`, autogenerate from
`app.models.Base.metadata`) and drop the `create_all` call.
