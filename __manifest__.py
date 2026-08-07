{
    "name": "Bosta Integration",
    "version": "18.0.8.0.0",
    "summary": "Bosta delivery persistence with safe lifecycle interpretation",
    "description": """
Bosta Integration
=================

Independent Odoo 18 foundation for direct Bosta API integration. Phase 2R
provides environment-only API-key configuration, a safe HTTP client, bounded
retry handling, paginated delivery retrieval, delivery-detail retrieval,
manager-only API connection testing, multi-company configuration isolation,
and redacted error handling.

Phase 3 adds source-agnostic persistent Bosta delivery and delivery-item models,
raw delivery type/state storage, normalized flow classification, return-link
schema, company-safe access controls, and inspection views.

Phase 4 adds pure Search/Details extraction orchestration, safe delivery/product/
pricing/timeline normalization, partial-update merge semantics, and protection
against unreliable pagination counts.

Phase 5 adds company-safe, idempotent persistence/upsert of normalized Bosta
records, deterministic delivery-item reconciliation, conservative stale-update
protection, safe manual manager synchronization, operational audit fields, and
advisory locking.

Phase 6 adds a pure, deterministic lifecycle interpreter that combines Bosta
flow, state, timestamps, and completed Details timeline evidence into persisted
lifecycle inspection fields. Stock, sale orders, customer/product mapping,
return linkage, profit/accounting, settlements, and scheduled jobs remain
intentionally deferred.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/bosta_security.xml",
        "security/ir.model.access.csv",
        "security/bosta_record_rules.xml",
        "views/bosta_config_views.xml",
        "views/bosta_delivery_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
