{
    "name": "Bosta Integration",
    "version": "18.0.2.0.0",
    "summary": "Secure company-specific configuration for Bosta Dashboard integration",
    "description": """
Bosta Integration
=================

Independent Odoo 18 module for a future Bosta Business Dashboard integration.
Phase 1 provides secure, company-specific Dashboard configuration, authenticated
password encryption, least-privilege access controls, and multi-company
isolation. Dashboard login, browser sessions, scraping, synchronization, orders,
inventory, accounting, and scheduled jobs are intentionally not implemented.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base"],
    "external_dependencies": {
        "python": ["cryptography"],
    },
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
