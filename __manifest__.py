{
    "name": "Bosta Integration",
    "version": "18.0.3.0.0",
    "summary": "Secure Bosta Dashboard authentication and encrypted browser sessions",
    "description": """
Bosta Integration
=================

Independent Odoo 18 module for secure Bosta Business Dashboard authentication.
Phase 2 adds Playwright Chromium lifecycle management, conservative Dashboard
login detection, encrypted browser storage-state persistence, saved-session
validation, manager-only authentication controls, and safe status reporting.
Order extraction, synchronization, product mapping, sales, inventory, returns,
accounting, settlements, profit, and scheduled jobs are intentionally excluded.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base"],
    "external_dependencies": {
        "python": ["cryptography", "playwright"],
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
