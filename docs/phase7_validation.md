# Phase 7 Validation Notes

## Scope

Target module version: `18.0.9.0.0`.
Dependencies: `base`, `stock`.

## Static validation performed in the supplied workspace

- Python `compileall`: PASS.
- Manifest parsing: PASS (`18.0.9.0.0`, `base,stock`, installable).
- XML parsing: 7 XML files, 0 parse errors.
- Static runtime scan: no direct `stock.quant`, `sale.order`, `account.move`,
  `res.partner`, or `ir.cron` references in Phase 7 runtime/config/view files.
- Static test discovery: 26 test files, 463 `test_*` methods. This is discovery
  only; Odoo-dependent tests were not executed in this workspace.

## Pure Phase 7 parser validation

A standalone harness loaded only the pure product-code helper/parser modules,
without Odoo or network access.

Result:

```text
14 cases
0 failures
0 errors
```

The unchanged pure Phase 2R-6 regression set was also loaded without Odoo and
run against the hardened tree:

```text
174 tests
0 failures
0 errors
```

Combined pure validation actually run for this hardened artifact:

```text
188 tests/cases
0 failures
0 errors
```

The cases cover leading-zero preservation, deterministic quantities,
multi-line all-or-nothing parsing, malformed-token rejection, no-space `3 ML`
tester naming, and both package-description shapes observed in the supplied
Bosta fixture.

## Supplied Bosta fixture validation

Only product/package descriptions were inspected; receiver PII was not printed.
Among package descriptions containing the observed `088.01-...` product token:

```text
129 descriptions inspected
128 fully deterministic
1 blocked as malformed/ambiguous
235 deterministic product lines
16 parsed codes preserved a leading zero
```

The malformed description is intentionally blocked rather than guessed.

## Supplied Odoo catalog analysis

The supplied product export was inspected before implementing bootstrap logic:

```text
371 product rows
186 distinct non-empty Internal References
185 conservative one-main / one-3ML-tester code pairs
1 non-paired/skipped product (Tips)
0 missing Internal References
```

The bootstrap accepts a trailing `3 ML` tester marker even without a preceding
space (for the observed `Soz3 ML` naming case). It does not rename products,
change Internal References, merge products, or guess ambiguous groups.

## Odoo test status

The current artifact workspace does not contain an importable Odoo runtime or a
Docker/Odoo executable, so the clean Odoo database suite was **not run here**.
Phase 7 must not be marked acceptance PASS until the project environment runs
the fresh `bosta_phase7_test` suite and obtains `0 failed, 0 error(s)`.

## Final hardening acceptance coverage

The final hardening keeps version `18.0.9.0.0` and dependencies `base,stock`.
No Phase 8 behavior was added.

Additional Odoo regression coverage now verifies:

- applied Source A / Transit A location snapshots survive a later config switch
  to Source B / Transit B;
- delivered finalization consumes from Transit A, keeps Transit B untouched,
  does not repeat the source deduction, and remains idempotent;
- the outgoing finalization operation type prefers the warehouse context of the
  historical outbound picking;
- blocked and pending effects with no outbound may safely adopt new configured
  locations on retry, and the effect audit locations match the picking created;
- source/transit effect locations are immutable after outbound application;
- mapped external-ID identity has highest precedence, followed by an existing
  mapped deterministic code identity from the same company/source, before any
  fresh exact `default_code` resolution;
- a manual code mapping continues to win when a later observation adds an
  external ID, without silently creating an external alias;
- mapping identity evolution does not create duplicate/random authoritative
  mappings and preserves company/source isolation;
- mapping/tester/stock blocking of one delivery does not prevent a following
  valid delivery in the same real persistence/sync callback stream;
- inventory-disabled Phase 5 sync creates no effects/pickings;
- inventory-enabled Phase 5 sync applies outbound once and an identical repeat
  sync does not create another picking/deduction;
- pending-inventory retry and normal sync continue to use the existing config
  advisory lock; a busy same-config retry does not enter the inventory engine;
- different company configurations remain inventory-independent;
- the existing Phase 5 regression
  `test_lock_released_if_running_audit_write_fails` remains present and
  unchanged for the clean Odoo suite.

The supplied real search fixture was re-checked with the final parser. Actual
result remains exactly:

```text
129 code-bearing descriptions inspected
128 fully deterministic
1 ambiguous and intentionally blocked
235 deterministic product lines
16 parsed codes preserved a leading zero
```

The clean Odoo database suite is still required before declaring full Phase 7
acceptance PASS.
