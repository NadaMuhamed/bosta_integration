# Bosta Integration Architecture

## Module identity

- **Technical module name:** `bosta_integration`
- **Configuration model:** `bosta.integration.config`
- **Target platform:** Odoo 18
- **Dashboard entry point:** `https://business.bosta.co/orders`

## Purpose and independence

`bosta_integration` is an independent module. The legacy `bosta_orders` module
is reference-only and is not a runtime dependency.

The module:

- does not depend on `bosta_orders`;
- does not import from `odoo.addons.bosta_orders`;
- does not reuse the legacy module at runtime; and
- must install when the legacy module is absent.

## Completed scope

### Phase 0 — Baseline

Phase 0 established the independent module skeleton, minimal `base` dependency,
company-scoped configuration model, clean package imports, namespace checks,
and planning documentation.

### Phase 1 — Configuration and security

Phase 1 added company-specific Dashboard configuration, Fernet password
encryption, credential audit fields, least-privilege groups, manager-only ACLs,
and allowed-company isolation.

### Phase 2 — Authentication and encrypted session lifecycle

Phase 2 adds:

- synchronous Playwright Chromium lifecycle management outside model methods;
- one saved-session validation followed by at most one fresh login attempt;
- conservative English/Arabic login locator candidates;
- multi-signal authenticated-page validation;
- encrypted Playwright storage-state persistence in PostgreSQL;
- manager-only Test Login and Reset Dashboard Session actions;
- safe status and timestamp fields;
- fail-closed OTP, CAPTCHA, blocked-account, connection, browser, and contract
  handling; and
- mocked automated tests with no real Bosta requests.

Phase 2 does **not** read the order table or extract order details.

## Runtime architecture

```text
bosta.integration.config
        ↓
DashboardAuthService
        ↓
DashboardSessionService
        ↓
BrowserFactory
        ↓
Playwright Chromium
```

The model performs access checks and controlled writes. It contains no direct
Playwright navigation or locator logic.

## Future phases

1. **Phase 3 — Order discovery and parsing:** only after separate approval.
2. **Later approved phases:** customer/product matching, sales documents,
   inventory, returns, accounting, reconciliation, profit, and scheduled jobs.

None of those future-phase services or models are implemented in Phase 2.
