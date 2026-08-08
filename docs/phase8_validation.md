# Phase 8 Validation Notes

## Scope

Target module version: `18.0.10.0.0`.
Dependencies: exactly `base`, `stock`.

Phase 8 implements safe original/return linking, explicit return cases,
pre-delivery RTO restoration, customer-return inspection and MAIN-only
restoration, and an exactly-once restoration ledger. It does not implement
Phase 9 financial scope or scheduling/webhook/queue behavior.

## Static validation actually run in this workspace

- Python `compileall`: PASS.
- Manifest parsing: PASS (`18.0.10.0.0`, `base,stock`, installable).
- XML parsing: PASS (8 XML files).
- Phase 8 standalone baseline/static tests: 10 tests, 0 failures, 0 errors.
- Static runtime scan: no direct `stock.quant` mutation and no `sale.order`,
  `account.move`, `res.partner`, or `ir.cron` references in Phase 8 runtime.
- Test discovery in the final tree: 29 `test_*.py` files and 518 `test_*`
  methods. This is discovery only for Odoo-dependent tests.

## Pure regression actually run

The prior pure API/normalization/lifecycle regression harness plus the 14 pure
Phase 7 product-code parser cases and 10 new Phase 8 static/baseline cases were
run without Odoo/network access.

```text
198 tests/cases
0 failures
0 errors
```

No live Bosta network request was made.

## Odoo-dependent Phase 8 coverage added

New tests cover safe linking/no businessReference guessing, manager/user access,
RTO physical completion, MAIN/TESTER exact restoration from Phase 7 snapshots,
historical mapping/location changes, repeated sync/action idempotency, missing
outbound evidence, customer-return inspection, MAIN-only restoration, explicit
partial quantities, over-return protection, lost/damaged/terminated/ambiguous
review behavior, immutable restoration snapshots, DB uniqueness, cumulative
over-restoration blocking, retry after fixing a missing link, per-delivery
blocking isolation, sync integration, same-config locking, company isolation,
and API failure safety.

## Full clean Odoo acceptance

Not run in this artifact workspace because no importable Odoo runtime or Docker
executable is available here. Phase 8 must **not** be declared full acceptance
PASS until the project environment runs a new clean database, for example:

```bash
docker compose run --rm web odoo \
  -d bosta_phase8_test \
  -i bosta_integration \
  --test-enable \
  --test-tags /bosta_integration \
  --without-demo=all \
  --stop-after-init \
  --log-level=test
```

Required final result: `0 failed, 0 error(s)`.
