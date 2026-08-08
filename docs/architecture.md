# Bosta Integration Architecture — Phase 8

## Scope

`bosta_integration` is an independent Odoo 18 module. Phases 0-6 provide the
direct Bosta API boundary, deterministic extraction/normalization, persistent
idempotent delivery sync, and lifecycle interpretation. Phase 7 adds product
mapping foundations and opt-in inventory effects. Phase 8 adds safe return
linking, auditable return cases, and exactly-once physical stock restoration.

Phase 8 does **not** create sale orders, partners, invoices, accounting entries,
profit/settlement calculations, cron jobs, webhooks, or background queues.

## Product mapping

Authoritative stock-changing resolution is deliberately conservative and
identity-aware:

1. A valid existing mapped external-ID identity for the same company/source wins.
2. Otherwise, a valid existing mapped deterministic code identity for the same
   company/source wins, even when the later observation also gains an external ID.
3. Only when neither authoritative identity exists may a safely parsed business
   code match `product.product.default_code`, and only when exactly one eligible
   explicit MAIN product exists.
4. Titles may be retained for review but never auto-create a stock-authoritative
   mapping.
5. Unmatched or conflicting products block the whole delivery inventory effect.

A code identity is not silently promoted into an external-ID alias. This prevents
a later stronger observation from replacing a prior manual/authoritative code
choice with a fresh `default_code` lookup.

Bosta `productInfo.productId` is an external identity and is never compared to
Odoo `default_code`.

MAIN/tester relationships are explicit persisted fields on `product.product`.
The manager-only bootstrap uses equal `default_code` plus the existing `3 ML`
name convention only as a one-time conservative initializer; runtime inventory
logic then relies on the persisted roles and links.

## Inventory safety boundary

Inventory is disabled by default. Enabling it requires the Bosta integration,
an explicit go-live cutoff, a company-safe internal source location, and a
company-safe Bosta Transit location. Multiple internal operation types must be
disambiguated explicitly.

Only forward deliveries can create outbound stock effects. Strong evidence that
merchandise left the business causes an idempotent supported Odoo stock
transfer:

```text
Internal Stock -> Bosta Transit
```

Each Bosta sale quantity moves the MAIN product and, when explicitly required,
the linked tester in the same quantity. Missing mappings, conflicts, missing
required tester links, insufficient stock, or reservation races block the
entire delivery. No partial delivery stock mutation is allowed.

A successfully delivered forward shipment may then be finalized:

```text
Bosta Transit -> Customers
```

RTO and customer-return Bosta records never create a second outbound deduction.
Phase 8 restores only from a safely linked original forward inventory effect.
Lost, damaged, terminated-after-pickup, and ambiguous states remain review-only.

## Idempotency and audit

`bosta.inventory.effect` is unique per company/delivery and stores the applied
picking references plus immutable product/quantity and source/transit location
snapshots after outbound application. Before outbound exists, a retry may refresh
the effect locations from current config; the outbound picking is then created
from exactly those audited locations. Once outbound exists, those location fields
cannot be replaced by later configuration changes. Delivered finalization always
uses the effect's historical transit snapshot as its source and prefers the
outgoing operation type from the warehouse context of the already-applied outbound
picking. Repeated sync/retry therefore cannot create a second source deduction or
final picking.

The normal delivery sync and manager retry action use the same existing
configuration advisory lock. Inventory work runs inside per-delivery database
savepoints. Expected mapping/stock blocks are recorded for review; unexpected
programming errors are not silently swallowed.

Stock is changed only by supported `stock.picking` / `stock.move` validation.
The integration never writes `stock.quant.quantity` directly.

## Package-description fallback

The Phase 7 pure parser accepts only fully deterministic observed package
formats. It preserves leading zeroes and refuses a complete package description
when any product association is malformed or ambiguous. Parsed package evidence
is represented inside the mapping/inventory layer and never fabricated as a
Phase 4 `bosta.delivery.item`.

## Phase 8 returns

`bosta.return.case` is the manager review boundary for reverse Bosta records.
The existing `bosta.delivery.original_delivery_id` remains the authoritative
original/return relation. Phase 8 never auto-links by `businessReference`,
receiver/customer data, COD, address, title, or date proximity. A manager may
select a candidate and use the explicit safe-link action; validation requires
the same company, a different record, a forward original, and an RTO/customer
return record. Conflicts are blocked rather than overwritten.

A completed pre-delivery RTO restores exactly the products and quantities that
the original Phase 7 outbound effect proves left stock:

```text
historical Bosta Transit -> historical Source
```

Both MAIN and TESTER are restored when their original outbound snapshot proves
they moved. Current product mappings and current config locations are never used
to reconstruct history.

A post-delivery customer return is different. Bosta logistics completion only
moves the case to inspection. The warehouse/manager must enter the physically
verified returned MAIN quantity per original delivered inventory line and accept
the inspection. Only then is MAIN restored:

```text
historical Customer location -> historical Source
```

TESTER is never restored for a post-delivery customer return. Zero/unknown
quantity, over-return risk, missing original stock evidence, or a non-delivered
original blocks restoration.

`bosta.return.restoration.effect` and its lines are the immutable restoration
ledger. Each line snapshots product, role, quantity, historical source and
destination, and the original Phase 7 inventory-effect line. Database uniqueness
prevents duplicate effects per return case, while row-locking original inventory
lines plus cumulative restoration checks prevent multiple return records from
over-restoring the same original quantity. Stock changes are created only via
normal `stock.picking` / `stock.move` validation.
