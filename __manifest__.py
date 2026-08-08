{
    "name": "Bosta Integration",
    "version": "18.0.11.0.0",
    "summary": "Bosta operational profitability, safe returns, and scheduled sync",
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

Phase 8 adds manager-controlled safe original/return linking, auditable return
cases, exactly-once RTO restoration from historical transit/source snapshots,
and post-delivery customer-return MAIN restoration only after explicit warehouse
inspection and approved returned quantities. It never auto-links by business
reference or PII and never restores TESTER on a customer return.

Phase 9 adds operational contribution snapshots, immutable product-cost snapshots,
Bosta logistics-fee evidence, return-aware cost credits, manager financial review,
and opt-in scheduled sync with bounded optional Details enrichment. It remains
independent from Sales, Accounting, Purchase, refunds, webhooks, and queues.
    """,
    "category": "Technical",
    "author": "My Company",
    "license": "LGPL-3",
    "depends": ["base", "stock"],
    "data": [
        "security/bosta_security.xml",
        "security/ir.model.access.csv",
        "security/bosta_record_rules.xml",
        "data/bosta_cron.xml",
        "views/product_product_views.xml",
        "views/bosta_config_views.xml",
        "views/bosta_delivery_views.xml",
        "views/bosta_product_mapping_views.xml",
        "views/bosta_inventory_views.xml",
        "views/bosta_return_views.xml",
        "views/bosta_financial_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
