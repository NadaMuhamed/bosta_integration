{
    "name": "Bosta Integration",
    "version": "18.0.9.0.0",
    "summary": "Bosta product mapping and idempotent inventory effects",
    "description": """
Bosta Integration
=================

Independent Odoo 18 foundation for direct Bosta API integration. Phases 0-6
provide safe API access, normalized persistent deliveries/items, idempotent
synchronization, and deterministic lifecycle interpretation.

Phase 7 adds explicit MAIN/3 ML tester product relationships, a conservative
one-time tester-link bootstrap, persistent source/company-aware Bosta product
mapping, deterministic package-description business-code parsing, an opt-in
inventory go-live cutoff, all-or-nothing delivery mapping/stock checks, and
idempotent stock transfers from Internal Stock to Bosta Transit. Successfully
delivered forward shipments may be finalized from Bosta Transit to Odoo's
Customer location.

Phase 7 intentionally does NOT restore RTO/customer returns, create sales
orders/customers/invoices, calculate profit/accounting, or add scheduled jobs.
Those concerns remain deferred to later phases.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base", "stock"],
    "data": [
        "security/bosta_security.xml",
        "security/ir.model.access.csv",
        "security/bosta_record_rules.xml",
        "views/product_product_views.xml",
        "views/bosta_config_views.xml",
        "views/bosta_delivery_views.xml",
        "views/bosta_product_mapping_views.xml",
        "views/bosta_inventory_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
