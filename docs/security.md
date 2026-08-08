# Bosta Integration Security — Phase 7

## Existing API boundary

The Bosta API key remains environment-only. Odoo stores only the configured
environment-variable name. Raw Authorization values, raw API payloads, and raw
timelines are not persisted by Phase 7.

## Access control

- `bosta.integration.config` stays manager-only.
- Tester bootstrap, inventory enablement/configuration, delivery sync, and
  pending-inventory retry are manager operations.
- `bosta.product.mapping` is manager-writable and ordinary integration-user
  read-only.
- `bosta.inventory.effect` and its audit lines are read-only to users/managers;
  only the internal inventory engine context writes them.
- Company record rules isolate mappings, effects, effect lines, deliveries, and
  configuration to allowed companies.

## Inventory safeguards

Inventory sync defaults to disabled and requires an explicit go-live cutoff,
source location, and Bosta Transit location. Historical records before the
cutoff remain reportable without mutating current stock.

Product resolution is fail-closed. Bosta external product IDs are not treated
as Odoo Internal References; title/fuzzy similarity cannot authorize stock
movement. Any unresolved item blocks the entire delivery effect.

Stock availability is checked in aggregate for MAIN and tester quantities, and
Odoo must fully reserve the complete picking before done quantities are set. A
reservation race therefore becomes a blocked inventory effect rather than a
partial/negative forced transfer.

Before outbound is applied, location audit fields may follow an explicitly changed
configuration. Once the outbound picking exists, source/transit audit locations are
immutable and must match that picking. Delivered finalization consumes only from
that historical transit snapshot, so a later configuration change cannot redirect
or rewrite an already-applied stock effect.

The module creates normal traceable Odoo pickings/moves and never directly
writes stock quants. Picking origins use only safe Bosta tracking identifiers;
receiver names, phones, addresses, API secrets, and raw payloads are not copied
into the Phase 7 inventory audit models.

## Phase boundary

Phase 7 does not create sales/accounting/customer documents and does not restore
RTO/customer-return stock. Return linkage and physical restoration remain
explicitly deferred to Phase 8.
