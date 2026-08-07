{
    "name": "Bosta Integration",
    "version": "18.0.4.0.0",
    "summary": "Secure Bosta API configuration, client, pagination, and connection testing",
    "description": """
Bosta Integration
=================

Independent Odoo 18 foundation for direct Bosta API integration. Phase 2R
provides environment-only API-key configuration, a safe HTTP client, bounded
retry handling, paginated delivery retrieval, delivery-detail retrieval,
manager-only API connection testing, multi-company configuration isolation,
and redacted error handling.

Order records, sales, inventory, returns, profit calculation, scheduled
synchronization, and other Phase 3+ business behavior are intentionally
excluded from this phase.
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
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
