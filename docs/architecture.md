# Bosta Integration Architecture

## Module identity

- **Technical module name:** `bosta_integration`
- **Configuration model:** `bosta.integration.config`
- **Target platform:** Odoo 18
- **Dashboard entry point:** `https://business.bosta.co/orders`

## Purpose and independence

`bosta_integration` is an independent module intended to authenticate to the
Bosta Business Dashboard and import complete Bosta order details in later
phases. The legacy `bosta_orders` module is read-only reference material.

The new module:

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

Phase 1 adds only secure configuration storage and access controls:

- one configuration per company;
- Dashboard URL and login settings;
- a non-stored new-password input;
- Fernet-authenticated password encryption using a server environment key;
- password-configured and credential-audit fields;
- explicit manager-only password clearing;
- disabled-by-default integration activation with credential validation;
- independent Bosta Integration User and Manager groups;
- manager-only configuration ACLs and menus; and
- allowed-company record isolation.

Phase 1 does **not** perform Dashboard authentication or network requests.

## Future phases

1. **Phase 2 — Authentication and session lifecycle:** browser automation,
   Dashboard login, session establishment, expiry detection, and safe runtime
   session-state handling.
2. **Phase 3 — Order discovery and parsing:** order-list navigation, complete
   detail extraction, normalization, snapshots, and idempotent import staging.
3. **Phase 4 — Odoo order integration:** customer/product matching and approved
   sales-document mapping.
4. **Later approved phases:** tracking, inventory, accounting, reconciliation,
   profit reporting, and scheduled jobs.

No future-phase implementation files are created in Phase 1.

## Security boundaries

Dashboard passwords are encrypted with `cryptography.fernet.Fernet`. The key is
read only from `BOSTA_DASHBOARD_ENCRYPTION_KEY` in the server environment. It is
not stored in PostgreSQL or Odoo system parameters.

Dashboard credentials, encryption keys, cookies, browser profiles, storage
state, and session files must never be committed. The repository ignores `.env`,
`playwright/.auth/`, `storage_state*.json`, and `browser_session*.json`.

The encrypted password is an internal model field and never appears in a form,
list, search view, notification, chatter message, or log. Ordinary integration
users receive no configuration-model ACL. Managers can access only records for
companies in their active allowed-company set.

## Phase 1 migration matrix

This matrix records architectural decisions only. Legacy business logic remains
unported.

| Old source file from `bosta_orders` | Reusable concept | New target in `bosta_integration` | Required adaptation | Decision | Phase |
|---|---|---|---|---|---|
| `models/bosta_config.py` | Company configuration, disabled-by-default activation, credential-safety ideas | `models/bosta_config.py` | Preserve `bosta.integration.config`; replace API-key, Shopify, sales, stock, accounting, and sync fields with Dashboard-only secure fields | Rewrite | Phase 1 |
| `security/bosta_security.xml` | Module category and User/Manager hierarchy | `security/bosta_security.xml` | Independent IDs; Manager implies User; no sales/stock groups and no automatic user assignment | Rewrite | Phase 1 |
| `security/bosta_record_rules.xml` | Allowed-company isolation | `security/bosta_record_rules.xml` | Apply only to the new configuration model and manager group | Adapt | Phase 1 |
| `security/ir.model.access.csv` | Manager CRUD pattern | `security/ir.model.access.csv` | Manager-only configuration ACL; no ordinary-user credential access | Rewrite | Phase 1 |
| `views/bosta_config_views.xml` | Configuration list/form/search organization | `views/bosta_config_views.xml` | Dashboard fields, masked non-stored input, manager-only menus/action, no encrypted field in views | Rewrite | Phase 1 |
| `tests/test_bosta_config.py` | Patched environment, company-isolation, and redaction test ideas | `tests/test_crypto_service.py`, `tests/test_configuration_security.py` | Replace public API-key assumptions with Fernet and Dashboard credentials | Adapt | Phase 1 |
| Shopify groups, fields, routes, and tests | None | None | Excluded from the independent Dashboard direction | Ignore | Not planned |
| Delivery, tracking, sales, inventory, accounting, cron, and profit code | Later domain ideas only | Future phase-specific files | Reassess and rewrite only after explicit requirements | Ignore for now | Phase 2+ |
