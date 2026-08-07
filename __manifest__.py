{
    "name": "Bosta Integration",
    "version": "18.0.5.0.0",
    "summary": "Secure Bosta API foundation with persistent delivery models",
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
schema, company-safe access controls, and inspection views. Synchronization,
partner/order/product creation, stock behavior, return lifecycle interpretation,
profit/accounting, settlements, and scheduled jobs remain intentionally deferred.
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
