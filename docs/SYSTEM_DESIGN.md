# System Design — Last-Mile Delivery Tracker

*(≈780 words)*

## Shape of the system

One FastAPI process serves both the JSON API and the static client, backed by
SQLAlchemy against SQLite locally and Postgres in production. A single service
keeps the free-tier deploy simple and removes CORS from the critical path; the
client is plain JS with no build step. Three roles — customer, agent, admin —
share one `users` table and are separated by JWT claims plus per-route
dependencies. Agents carry an `agent_profiles` row holding the availability,
capacity and position that dispatch reasons about.

## Rate calculation engine

The engine is a pure function of the database, not the code. Five steps: resolve
both pincodes to zones; decide scope (`INTRA` when they match, `INTER` otherwise);
derive billable weight; select a rate card; add COD.

Volumetric weight is `L×B×H ÷ divisor`, where the divisor is a row in
`system_settings` rather than a literal — operations can move from 5000 to 4000
without a deploy. The higher of actual and volumetric weight is taken, then
rounded up to a configurable slab step (default 0.5 kg), with an epsilon guard so
an exact 2.0 kg never creeps to 2.5.

Rate cards are keyed on `(order_type, scope, from_zone, to_zone)`. B2B and B2C
intra- and inter-zone rates are four independent rows, and a card with both zones
set acts as a lane override. Lookup pulls every active match and sorts by
specificity — exact lane, origin lane, zone-agnostic default — so a promotional
CHN-N → CHN-S rate beats the generic inter-zone card with no conditional logic.
Freight is a slab calculation: `base_price + ceil((billable − base_weight) ÷
slab) × slab_price`, floored at the card's minimum, with an optional fuel
percentage on top.

COD is a separate table keyed by order type, because the surcharge is commercial
policy rather than a freight input: the fee is the greater of a flat amount and a
percentage of freight, clamped to a floor and an optional cap. Prepaid orders
never touch it.

Quoting and booking share one code path. `POST /orders/quote` returns the full
breakdown — both weights, which one won, the chosen card, every line item — and
that is what the booking screen renders before the customer confirms. On confirm
the engine runs again server-side and the breakdown is frozen onto the order, so
repricing a card later never rewrites shipments already sold.

## Zone detection

Zones are geography-as-data. A pincode resolves through the `areas` table to
exactly one zone; unmapped pincodes fall back to the longest shared prefix among
serviceable areas, handling a new pincode inside an already-mapped sector.
Anything else raises `ZoneNotFound`, surfaced as a 422 the booking form shows
inline. Admins remap pincodes at runtime. Zones carry an optional centroid, the
distance fallback when neither order nor agent has GPS.

## Auto-assignment

Candidates are agents on duty, active, and below capacity. They are ranked on a
lexicographic tuple rather than a weighted score, because weights hide why a
particular agent was chosen: zone match, then haversine distance from the agent's
last fix to the pickup, then load ratio, then agent id as a deterministic
tie-break. Distance degrades gracefully — GPS fix, else zone centroid, else a
sentinel that sinks the candidate below anyone locatable. Capacity is a counter
moved on attach and release, so an agent who fails an attempt gets the slot back
immediately. The winning candidate's reasoning ("in pickup zone, 4.3 km away,
load 1/5") is written into the tracking note, making assignments auditable.

## Status lifecycle and immutable history

Transitions are an explicit adjacency map; anything else is rejected with a 409.
Agents are further restricted to the five field statuses, so they cannot cancel
or reassign. Admins can force any status, but the override bypasses only the map
— it still writes a checkpoint flagged `is_override` with the admin's name.

`tracking_events` is append-only. Updates and deletes raise at the ORM layer, and
each row stores a SHA-256 over its contents plus the previous event's hash. That
chain covers what the ORM guard cannot: someone editing a row directly in the
database. An admin endpoint recomputes the chain and reports the first break.
Timestamps are normalised to a UTC epoch before hashing, so the chain verifies
identically on SQLite and Postgres.

## Failed delivery

A failure records its reason, increments the attempt counter, releases the
agent's capacity and notifies the customer. The customer supplies a new date; the
order moves `FAILED → RESCHEDULED → ASSIGNED`, a `reschedule_requests` row
captures the old and new agent, and assignment re-runs excluding the agent who
just failed — falling back to them only if nobody else is free, since a stranded
parcel is worse than a repeat attempt. Notifications are persisted before
dispatch and sent on a background task, so a slow SMTP handshake never blocks the
response, and an unconfigured provider still leaves an auditable record.
