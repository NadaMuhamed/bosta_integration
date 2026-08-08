# Bosta Integration Architecture — Phase 7

## Scope

`bosta_integration` is an independent Odoo 18 module. Phases 0-6 provide the
direct Bosta API boundary, deterministic extraction/normalization, persistent
idempotent delivery sync, and lifecycle interpretation. Phase 7 adds product
mapping foundations and opt-in inventory effects only.

Phase 7 does **not** create sale orders, partners, invoices, accounting entries,
profit calculations, or physical return/RTO restoration.

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

Returning/returned-to-origin, lost, damaged, or terminated-after-pickup goods
are not restored to source stock in Phase 7. RTO and customer-return Bosta
records never create a second outbound deduction. Physical return restoration
is Phase 8.

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
