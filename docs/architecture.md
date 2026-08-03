# Bosta Integration Architecture

## Module identity

- **Technical module name:** `bosta_integration`
- **Primary configuration model:** `bosta.integration.config`
- **Odoo version:** 18

## Purpose

`bosta_integration` is an independent Odoo module intended to integrate with the
Bosta Business Dashboard and, in future phases, import complete Bosta order
details into Odoo.

The legacy `bosta_orders` module is read-only reference material. The new module
must install and run without that module being installed, has no dependency on
it, and does not import from it.

## Phase 0 scope

Phase 0 establishes only a clean, installable baseline:

- minimal manifest dependencies;
- a minimal company-scoped configuration model;
- administrator-only baseline access and menus;
- clean Python package imports;
- an architecture and migration plan;
- basic namespace and manifest tests; and
- removal of generated or machine-local files.

Phase 0 deliberately excludes Dashboard login, credentials, cookies, browser
sessions, scraping, order import, inventory, accounting, and scheduled jobs.

## Future phases

The exact phase boundaries may be refined after the Dashboard behavior and data
contract are documented.

1. **Phase 1 — Configuration and security:** independent user/manager groups,
   company isolation, and secure Dashboard configuration fields.
2. **Phase 2 — Authentication and session lifecycle:** browser automation,
   session establishment, expiry detection, redacted diagnostics, and secure
   local runtime storage.
3. **Phase 3 — Order discovery and detail parsing:** list navigation, complete
   order-detail extraction, normalization, source snapshots, and idempotency.
4. **Phase 4 — Odoo order integration:** customer and product matching, import
   records, sales-document mapping, status history, and reconciliation.
5. **Later phases — Optional operations:** inventory, accounting, and cron jobs
   only after explicit requirements and safety controls are approved.

## Secret-handling policy

Dashboard usernames, passwords, cookies, authentication state, browser profiles,
and session files must never be committed to Git. Runtime artifacts such as
`playwright/.auth/`, `storage_state*.json`, and `browser_session*.json` are
ignored by the repository.

## Legacy migration matrix

This matrix is planning only. No legacy business logic is ported in Phase 0.

| Old source file from `bosta_orders` | Reusable logic | Future target in `bosta_integration` | Required adaptation | Decision | Future phase |
|---|---|---|---|---|---|
| `__manifest__.py` | Odoo metadata conventions | `__manifest__.py` | Independent dependencies, data paths, and phase descriptions | Rewrite | Phase 0 and each later phase |
| `models/bosta_config.py` | Company-scoped configuration patterns | `models/bosta_config.py` | Keep `bosta.integration.config`; replace API and Shopify assumptions with secure Dashboard settings | Rewrite | Phase 1 |
| `views/bosta_config_views.xml` | General list/form/search layout | `views/bosta_config_views.xml` | New fields, new XML IDs, and independent groups | Adapt | Phase 1 |
| `security/bosta_security.xml` | User/manager role concept | Future security XML | Recreate with least privilege and no legacy implied groups | Rewrite | Phase 1 |
| `security/ir.model.access.csv` | ACL structure | `security/ir.model.access.csv` | Use independent groups and new model IDs | Rewrite | Phase 1 |
| `security/bosta_record_rules.xml` | Multi-company isolation concept | Future record-rules XML | Recreate for new models and groups | Adapt | Phase 1 |
| `services/bosta_client.py` | Retry, logging, and response validation patterns | Future Dashboard browser/session services | Replace API-key requests with Dashboard authentication and session handling | Rewrite | Phase 2 |
| `services/exceptions.py` | Exception taxonomy concept | Future `services/exceptions.py` | Add login, session, browser, parsing, and import errors | Adapt | Phase 2 |
| `controllers/shopify_inbound_controller.py` and related Shopify files | None for the current direction | None | Excluded legacy inbound architecture | Ignore | Not planned |
| `services/bosta_mapper.py` | Remote-to-Odoo mapping separation | Future order parser/mapper | Rebuild against observed Dashboard order-detail data | Adapt | Phase 3 |
| `services/order_sync_service.py` | Idempotent orchestration concept | Future order import service | Rebuild around Dashboard records and independent models | Adapt | Phase 3 |
| `services/customer_matcher.py` and `services/phone_utils.py` | Matching and normalization concepts | Future customer utilities | Reassess privacy, matching priority, and duplicate safety | Adapt | Phase 3 |
| `services/product_matcher.py` and `models/bosta_sku_mapping.py` | SKU matching concepts | Future product/SKU components | Recreate after the imported order schema is stable | Adapt | Phase 3 or 4 |
| `models/bosta_delivery.py` | Remote identity and idempotency concepts | Future Bosta order/import model | Replace delivery-API assumptions with Dashboard order state | Rewrite | Phase 3 |
| `models/bosta_status_mapping.py` and `models/bosta_status_history.py` | Status normalization/history concepts | Future status models | Validate semantics against Dashboard evidence | Adapt | Phase 4 |
| `models/bosta_sync_log.py` | Operational audit-log concept | Future import log model | Redesign for login, scraping, parsing, import, and redacted diagnostics | Adapt | Phase 2 or 3 |
| `services/tracking_*` and `services/status_catalog.py` | Idempotent status processing concepts | Future status services | Revalidate all status codes and actions | Adapt | Phase 4 |
| `models/sale_order.py`, `models/sale_order_line.py`, and `models/res_partner.py` | Odoo extension patterns | Future sales/customer integration | Add only after import requirements are stable | Adapt | Phase 4 |
| `models/stock_picking.py`, inventory services, and Phase 9 security data | Guarded stock-flow concepts | Future inventory integration | Full redesign; do not add stock dependencies prematurely | Rewrite | Later inventory phase |
| Accounting configuration concepts | Disabled-by-default safety approach | Future accounting components | Add only after explicit accounting requirements | Rewrite | Later accounting phase |
| Legacy tests | Edge cases and testing patterns | New phase-specific tests | Rewrite imports, fixtures, models, and expected behavior | Adapt | Every future phase |
| Legacy documentation | Historical domain notes | New documentation | Rewrite around Dashboard architecture and security | Adapt | Relevant future phase |
