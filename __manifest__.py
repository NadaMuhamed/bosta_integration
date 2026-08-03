{
    "name": "Bosta Integration",
    "version": "18.0.1.0.0",
    "summary": "Independent baseline for Bosta Dashboard integration",
    "description": """
Bosta Integration
=================

Independent Odoo 18 module baseline prepared for a future Bosta Dashboard
integration. Phase 0 provides only the module skeleton and a minimal
company-scoped configuration model. Authentication, sessions, order imports,
inventory, accounting, and scheduled jobs are intentionally not implemented.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/ir.model.access.csv",
        "views/bosta_config_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
