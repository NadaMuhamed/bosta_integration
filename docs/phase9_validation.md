# Phase 9 Validation Notes

## Scope

Target module version: `18.0.11.0.0`.
Dependencies: exactly `base`, `stock`.

Phase 9 provides direct Bosta API integration, normalized persistent
Deliveries/items, deterministic lifecycle handling, Phase 7 product mapping and
inventory effects, Phase 8 safe returns/restoration, operational financial
snapshots with Bosta fee evidence, and opt-in scheduled sync with bounded
Details enrichment.

## Phase 8 -> Phase 9 upgrade-safety fix

`bosta.return.case.original_financial_id` no longer declares a registry-time
dotted dependency on `original_delivery_id.financial_ids`. It depends only on
`original_delivery_id` and resolves `bosta.delivery.financial` by an explicit
ORM search on both `company_id` and `delivery_id`, with `limit=1`. The compute
does not use `sudo()`, so existing access controls and record rules remain in
force. `bosta.delivery.financial_ids` remains available for the manager UI and
the `(company_id, delivery_id)` database uniqueness remains authoritative.

The other Phase 9 dotted compute dependency, `adjustment_ids.amount`, was also
removed. `_compute_adjustment_totals` now depends on `adjustment_ids` and
`contribution_profit`. Financial adjustments are immutable and their amount is
set at creation, so relation creation/removal is the required invalidation
boundary while avoiding a Phase-9-only dotted registry dependency.

No new Phase 9 migration, init hook, post-init hook, automatic finance
finalization, return/restoration replay, stock replay, or network-on-registry
behavior was added. Existing historical deliveries, mappings, stock effects,
return cases, and restoration effects are not deleted or recreated by this
change. Missing historical financial evidence remains missing/not-ready rather
than being synthesized as zero.

## Financial and operational safety retained

- COD is not recognized as revenue automatically.
- Missing financial evidence remains distinct from explicit zero.
- Product costs remain snapshotted; finalized base history remains immutable.
- Later authoritative return effects use idempotent adjustments/revisions.
- RTO credits require actual Phase 8 restoration and may credit MAIN + TESTER.
- Accepted customer returns credit MAIN only; TESTER remains consumed.
- Financial credit remains capped by historical gross COGS.
- Logistics/return/compensation values are not invented without evidence.
- API financial evidence fields remain protected from ordinary create/write.
- Manager-only overrides, company isolation, safe return linking, advisory
  locking, and existing stock/return behavior are unchanged.

## Tests added for this fix

Static/baseline guards now verify:

- `original_financial_id` has no `original_delivery_id.financial_ids` dependency;
- its search is explicitly scoped by both company and original delivery;
- no model contains a dotted `@api.depends` dependency;
- the manifest is exactly version `18.0.11.0.0` with dependencies
  `['base', 'stock']`;
- manifest summary/description describe Phase 9 rather than the old Phase 2R
  copy;
- inherited view XPaths do not use `@string` selectors;
- the deployable source tree contains no `.git`, `__MACOSX`, `.DS_Store`,
  `__pycache__`, or `.pyc` artifacts.

Odoo runtime regression tests were added for:

1. no financial snapshot -> `original_financial_id` is false;
2. existing original snapshot -> it resolves correctly;
3. even a simulated legacy/corrupt cross-company financial row cannot resolve.

## Validation actually run for this corrected artifact

This workspace has no importable `odoo` Python package, no `odoo` CLI, and no
Docker executable. Therefore Odoo `TransactionCase`, clean-install, full-suite,
and real-database upgrade commands were **not run** for this corrected artifact.
They must not be reported as PASS from this workspace.

The non-Odoo test harness available here was run after the code changes and
reported:

```text
Ran 224 tests in 0.123s
OK
PURE_RESULT tests=224 failures=0 errors=0 skipped=0
```

The final packaging pass additionally runs Python source compilation, XML parse,
manifest validation, XPath/dependency static guards, and ZIP-entry artifact
validation. See the delivery report accompanying the ZIP for those exact final
outputs.

## Manual real database proof still required

Run the requested upgrade manually against the existing database after placing
the corrected module in the configured addons path:

```bash
odoo \
  -d odoo18_test \
  -u bosta_integration \
  --without-demo=all \
  --stop-after-init
```

Only that successful command can establish the real Phase 8 -> Phase 9 database
upgrade result and refresh the Odoo Apps metadata through the normal module
upgrade path. No direct SQL update of `ir_module_module` is required or used.
