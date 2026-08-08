# Phase 9 Validation Notes

## Scope

Target module version: `18.0.11.0.0`.
Dependencies: exactly `base`, `stock`.

Phase 9 adds an operational contribution layer for Bosta deliveries, immutable
product-cost snapshots, authoritative/missing Bosta fee semantics, Phase 8
return-aware cost credits, manager financial review, one shared opt-in scheduled
sync cron, and bounded opt-in Details enrichment. It does not create Sales,
Accounting, Purchase, customer, refund, payment, or journal-entry records.

## Financial safety rules implemented

- COD remains operational collection data and is never recognized revenue by
  default. A manager must explicitly confirm revenue or explicitly confirm COD
  as revenue.
- Missing revenue, product cost, logistics cost, return fee, or required
  compensation is never silently converted to zero.
- API monetary presence flags distinguish a real explicit zero from a missing
  field. These flags and the corresponding API financial evidence are protected
  from direct manual writes; reviewed overrides live on the financial snapshot.
- `product.standard_price` is snapshotted only when it is a finite positive
  configured inventory cost. A zero/unavailable standard cost remains
  incomplete unless a manager explicitly confirms a cost override (including an
  intentional zero override).
- Historical cost lines use the Phase 7 inventory-effect product/quantity
  snapshots, never current mapping resolution.
- Restoration credits use only applied Phase 8 restoration-effect lines linked
  to the original Phase 7 inventory-effect line and role. Credits are capped at
  original snapshotted quantity/cost.
- RTO can credit MAIN and TESTER only after physical Phase 8 restoration. The
  original logistics charge remains.
- Post-delivery customer return credits only the actually restored MAIN quantity;
  TESTER remains consumed. An additional return fee is unknown unless explicitly
  authoritative/manager-confirmed.
- A Bosta `shipmentFees` total is used without adding pricing aliases/components
  again. Currency must be explicit and compatible; otherwise the result remains
  incomplete/review-required.
- Finalized financial snapshots are not silently rewritten by later product cost
  or mapping changes.

## Scheduled sync / locking

One shared `ir.cron` wakes every five minutes and selects only active,
integration-enabled, auto-sync-enabled configurations that are due. Auto sync is
OFF by default, and enabling it schedules the first due time explicitly.

Cron calls the existing `action_sync_bosta_deliveries()` path. Optional Details
financial enrichment is requested through context and runs *inside that same
sync action while the accepted advisory lock is still held*. Therefore Search,
persistence, inventory, returns, finance, and the optional bounded Details pass
cannot overlap another manual/cron sync for the same configuration. Different
configurations remain independent.

Details enrichment is OFF by default, capped by configuration (1..200, default
50), selects only financially relevant forward deliveries missing authoritative
shipment fees, and will not re-enrich the same record more often than hourly.
Authentication/rate-limit failures stop the current enrichment batch safely;
ordinary per-delivery API/persistence failures do not create fake fees.

## Static validation actually run in this workspace

- `python3 -m compileall -q .`: PASS.
- Manifest parse: PASS.
  - version: `18.0.11.0.0`
  - depends: `['base', 'stock']`
  - installable: `True`
- XML parse: PASS (`10` XML files).
- `git diff --check`: PASS.
- Static test discovery: `31` `test_*.py` files and `573` test methods.
  Discovery is not execution of Odoo-dependent tests.

## Pure regression actually run

The same non-Odoo pure regression surface from the accepted Phase 8 artifact was
re-run together with the Phase 9 static baseline. The harness covers API client,
pagination, normalization, Search/Details separation, lifecycle interpretation,
real payload shapes, product-code parser behavior, Phase 8 static boundaries,
and the new Phase 9 static/architecture guards.

```text
228 tests/cases
0 failures
0 errors
```

This consists of the prior 198 pure Phase 8 cases plus 30 Phase 9 baseline cases.
No live Bosta network request was made.

## Odoo-dependent Phase 9 coverage added

`tests/test_phase9_financial_runtime.py` adds runtime coverage for recognized
revenue confirmation, COD non-default behavior, missing-versus-explicit-zero
fees, MAIN/TESTER COGS, quantity multiplication, immutable standard-cost
snapshots, duplicate financial uniqueness, stock non-mutation, audited manager
cost overrides, ordinary-user denial, RTO MAIN/TESTER cost credit and shipping
retention, repeated RTO idempotency, customer-return MAIN-only/partial credits,
return rejection, safe-link requirements, lost-shipment compensation review,
finalization stability, cron opt-in, due/not-due behavior, missing-key safety,
and per-config cron failure isolation.

These Odoo-dependent tests are syntax-compiled in this workspace but were **not
executed** because this environment has neither an importable Odoo runtime nor a
Docker executable.

## Clean install / full Odoo acceptance

Not run here. Do not mark Phase 9 full acceptance PASS until a project environment
with Odoo/Docker runs a fresh database:

```bash
docker compose run --rm web odoo \
  -d bosta_phase9_test \
  -i bosta_integration \
  --test-enable \
  --test-tags /bosta_integration \
  --without-demo=all \
  --stop-after-init \
  --log-level=test
```

Required actual final result: `0 failed, 0 error(s)`.

## Upgrade-safety review

The Phase 8 -> Phase 9 schema change is additive. Existing deliveries, item
mappings, inventory effects, return cases, and restoration effects remain the
source history. Installation/upgrade does not make Bosta network calls, replay
stock, create return restorations, or mass-finalize historical financial data.
Existing historical fee fields deliberately do not receive synthetic presence
flags during upgrade; they remain incomplete until authoritative re-sync/Details
evidence or an explicit manager confirmation is available.
