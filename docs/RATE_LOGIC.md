# Rate Calculation Logic

Nothing below is hardcoded. Zones, pincode mappings, rate cards, COD rules and
the two engine constants all live in the database and are editable from the admin
screens at runtime. Implementation: `backend/app/services/rate_engine.py`.

## The pipeline

```
pickup_pincode ──┐
                 ├──► zone detection ──► scope = INTRA | INTER ──┐
drop_pincode ────┘                                               │
                                                                 ▼
L × B × H ──► volumetric weight ──┐                    rate card lookup
                                  ├──► billable weight ──► freight ──► fuel
actual weight ────────────────────┘                                     │
                                                                        ▼
payment_type == COD ──► COD rule for order_type ──► surcharge ──► TOTAL
```

## Step 1 — Zone detection

1. Exact match on a serviceable `areas.pincode` → that area's zone.
2. Otherwise the serviceable area sharing the longest prefix (minimum 3 digits)
   with the given pincode — this covers a new pincode inside a mapped sector.
3. Otherwise `ZoneNotFound` → HTTP 422, shown inline on the booking form.

`scope = INTRA` when pickup and drop resolve to the same zone, else `INTER`.

## Step 2 — Billable weight

```
volumetric_kg = (L × B × H) / volumetric_divisor        # divisor from system_settings, default 5000
billable_raw  = max(actual_kg, volumetric_kg)
billable_kg   = ceil(billable_raw / rounding_step) × rounding_step   # step from system_settings, default 0.5
weight_basis  = "VOLUMETRIC" if volumetric > actual else "ACTUAL"
```

The rounding carries an epsilon guard (`1e-9`) so a value that is already exactly
on a step — 2.0 kg at a 0.5 step — does not creep up to 2.5 through float error.

## Step 3 — Rate card selection

Filter to active cards where `order_type` and `scope` match and each zone column
is either null or equal to this shipment's zone. Sort the survivors by
specificity and take the first:

| Precedence | `from_zone_id` | `to_zone_id` | Meaning |
|---|---|---|---|
| 1 (highest) | set | set | exact lane, e.g. a CHN-N → CHN-S promo |
| 2 | set | null | everything leaving this origin |
| 3 | null | null | the default card for this type + scope |

No matching card is a configuration error (422), not a silent fallback to a
hardcoded price — the operator is told which combination is unconfigured.

## Step 4 — Freight

```
extra   = max(0, billable_kg − base_weight_kg)
slabs   = ceil(extra / increment_weight_kg)
freight = base_price + slabs × increment_price
freight = max(freight, min_charge)
fuel    = freight × fuel_surcharge_pct / 100
```

## Step 5 — COD surcharge

Applied only when `payment_type == COD`, using the rule for that order type:

```
fee = max(flat_fee, (freight + fuel) × percent_of_freight / 100)
fee = max(fee, min_fee)
fee = min(fee, max_fee)        # when a cap is configured
```

B2B and B2C have independent rules, so a business account can carry a higher flat
fee with a lower percentage than retail.

## Step 6 — Total

```
total = freight + fuel + cod
```

The full breakdown — both weights, which one won, the divisor used, the card
chosen, and every line item — is returned by `POST /api/orders/quote` and shown
before the customer confirms. On confirm the engine runs again server-side and
the breakdown is serialised into `orders.charge_breakdown`, so later edits to a
rate card never change what an existing shipment was sold for.

---

## Worked example — the seeded demo

**Input:** 600001 (Parrys) → 600020 (Adyar), 40 × 30 × 20 cm, 2 kg actual, B2C, COD.

| Step | Working | Result |
|---|---|---|
| Zones | 600001 → CHN-N, 600020 → CHN-S | different ⇒ `INTER` |
| Volumetric | 40 × 30 × 20 = 24,000 cm³ ÷ 5000 | 4.8 kg |
| Billable | max(2.0, 4.8) = 4.8, rounded up to 0.5 | **5.0 kg**, basis `VOLUMETRIC` |
| Card | B2C + INTER, no lane override | "B2C Inter-zone standard" |
| Freight | extra = 5.0 − 1.0 = 4.0 kg → ceil(4.0/0.5) = 8 slabs → 75 + 8 × 28 | ₹299.00 |
| Fuel | 299 × 6% | ₹17.94 |
| COD | max(₹35 flat, 316.94 × 2% = ₹6.34) | ₹35.00 |
| **Total** | 299.00 + 17.94 + 35.00 | **₹351.94** |

**Same parcel as B2B:** the B2B inter card includes 5 kg in its ₹230 base, so
5.0 kg billable needs no slabs — freight ₹230.00, fuel ₹13.80, COD
`max(₹60, 243.80 × 1.5% = ₹3.66)` = ₹60.00, **total ₹303.80**. The heavier base
allowance is what makes bulk shipping cheaper per parcel.

**Same parcel prepaid:** COD drops out entirely — **₹316.94**.

## Worked example — actual weight wins

10 kg of machine parts in a 20 × 20 × 20 cm box, B2C, inter-zone, prepaid:

- volumetric = 8,000 ÷ 5000 = 1.6 kg
- billable = max(10, 1.6) = **10 kg**, basis `ACTUAL`
- freight = 75 + ceil(9 / 0.5) × 28 = 75 + 18 × 28 = ₹579.00
- fuel = ₹34.74 → **total ₹613.74**

## Tuning the engine without a deploy

| Change | Where |
|---|---|
| Cheaper inter-zone B2C | Rate cards → edit base/slab price |
| Promotional lane rate | Rate cards → add a card with both zones set |
| Stricter volumetric billing | Rate cards → engine settings → `volumetric_divisor` 5000 → 4000 |
| Bill in whole kilos | Engine settings → `billable_rounding_step` → `1` |
| Raise the COD cap | COD surcharge → `max_fee` |
| Open a new territory | Zones & areas → add zone, map pincodes, add its rate cards |

## Tests

`backend/tests/test_engine.py` covers this file directly: divisor
configurability, actual-vs-volumetric selection, the rounding edge case, slab
arithmetic, lane-override precedence, minimum-charge floors, B2B/B2C divergence,
COD flat-vs-percentage and capping, and the missing-card error path. Run
`pytest -q` from `backend/`.
