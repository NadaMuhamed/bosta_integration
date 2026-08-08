# Bosta Integration Architecture — Phase 9

## Scope

`bosta_integration` is an independent Odoo 18 module. Phases 0-6 provide the
direct Bosta API boundary, deterministic extraction/normalization, persistent
idempotent delivery sync, and lifecycle interpretation. Phase 7 adds explicit
product mapping/tester relationships and opt-in inventory effects. Phase 8 adds
safe return linking and exactly-once physical stock restoration. Phase 9 extends
that accepted flow with operational delivery contribution snapshots and opt-in
scheduled synchronization.

Phase 9 is **not accounting**. It does not create sale orders, partners,
invoices, credit notes, journal entries, payments, refunds, purchase costing,
or tax/company-net-profit calculations.

## Existing sync path remains authoritative

There is still one delivery-sync business path:

```text
Bosta Search
  -> normalization
  -> persistence / lifecycle
  -> Phase 7 inventory evaluation
  -> Phase 8 return evaluation
  -> Phase 9 financial evaluation
```

Manual sync and scheduled sync both call `action_sync_bosta_deliveries()` and
therefore use the same accepted PostgreSQL advisory lock. Cron does not contain a
second persistence/inventory/return implementation.

## Product mapping and inventory safety

Product resolution remains the accepted Phase 7 design: mapped external identity,
then mapped deterministic code identity, then an exact unambiguous MAIN product
code fallback. Titles never create stock-authoritative mappings. MAIN/tester
relationships are explicit persisted product relationships.

Inventory remains opt-in with a go-live cutoff. Forward stock leaves through
supported Odoo pickings from the configured internal source to Bosta Transit,
then successful delivery may finalize from historical Transit to Customer.
`stock.quant` is never written directly.

Phase 8 reverse flows still own physical restoration. RTO restores exactly the
historical MAIN/TESTER quantities proven to have left. A post-delivery customer
return restores only warehouse-inspected/accepted MAIN quantity and never the
TESTER. Financial code never reconstructs restoration from lifecycle text.

## Operational financial snapshot

`bosta.delivery.financial` is unique per company/original forward delivery.
`bosta.delivery.financial.line` snapshots each Phase 7 inventory-effect line and
role with product, quantity, unit cost, gross cost, accepted restoration credit,
and net cost.

Recognized revenue is intentionally separate from COD. Positive or zero COD is
not revenue unless an explicit manager action confirms that interpretation.
Missing revenue remains unavailable.

Product COGS is snapshotted from the actual Phase 7 product snapshot. A finite
positive `product.standard_price` may be used and is named truthfully as
`product_standard_price`; it is not called purchase-invoice cost. Missing/zero
unconfigured product cost remains incomplete until an explicit audited manager
cost override is supplied.

Bosta fee handling prefers an explicit `shipmentFees` total only when its
presence and compatible currency are known. Alias/components are not added on
top of that total. Component-only pricing is partial evidence, not a fabricated
total. API financial evidence fields are persistence-controlled so managers use
audited financial override actions instead of silently rewriting Bosta evidence.

When all required inputs are authoritative:

```text
net COGS = gross COGS - accepted Phase 8 restoration cost credits

Delivery Contribution =
    recognized revenue
    - net COGS
    - Bosta logistics cost
    - explicit return fees
    + explicit compensation
```

Unknown inputs do not enter the formula as zero.

## Return-aware finance

Financial return effects always target the safely linked original forward
delivery. `businessReference`, receiver data, COD, address, and date proximity
are never used to link financial returns.

A completed pre-delivery RTO may credit MAIN and TESTER only where applied Phase
8 restoration-effect lines prove both were physically restored. The original
Bosta logistics charge remains.

A post-delivery customer return credits only the accepted restored MAIN quantity.
TESTER cost stays consumed. The original forward logistics charge remains, and
an additional return fee is unknown until authoritative evidence or an explicit
manager confirmation exists.

Lost/damaged inventory receives no restoration credit and no invented Bosta
compensation. Ambiguous/contradictory evidence is review-required.

## Financial history and security

Financial records are company-isolated. Integration users are read-only;
financial confirmations and overrides require the manager group and retain user,
timestamp, source, and safe reason audit fields. Financial records store no API
key, Authorization header, raw payload, customer phone, or customer address.

Finalized snapshots are frozen. Current product cost, mapping, tester relation,
or configuration changes do not rewrite finalized historical cost lines.

## Scheduled sync and Details enrichment

One shared five-minute `ir.cron` selects due configs. `auto_sync_enabled` defaults
False, each config has its own interval (minimum five minutes), and every attempt
advances the next due time so failures do not create a tight retry loop.

Optional financial Details enrichment is also OFF by default. Cron requests it
through context on the same `action_sync_bosta_deliveries()` call, so the bounded
Details pass executes before that action releases the same advisory lock. This
prevents manual+cron or cron+cron overlap on one configuration.

Search extraction itself never calls Details. The enrichment service selects only
financially relevant forward deliveries missing authoritative fee evidence,
respects a bounded batch limit, avoids re-enriching a record more often than
hourly, and stops safely on global authentication/rate-limit conditions.
