"""Bounded explicit Details enrichment for missing Phase 9 financial fields."""

from datetime import timedelta

from odoo import fields

from .exceptions import (
    BostaApiAuthenticationError,
    BostaApiRateLimitError,
    BostaApiError,
    BostaPersistenceError,
)
from .bosta_financial_service import BostaFinancialService


class BostaFinancialEnrichmentService:
    def __init__(self, env, config, extraction, persistence):
        config.ensure_one()
        self.env = env
        self.config = config
        self.extraction = extraction
        self.persistence = persistence
        self.finance = BostaFinancialService(env, config)

    def run(self):
        if not self.config.financial_details_enrichment_enabled:
            return {"attempted": 0, "updated": 0, "errors": 0, "stopped": False, "stop_status": False}
        limit = min(max(int(self.config.financial_details_batch_limit or 1), 1), 200)
        Delivery = self.env["bosta.delivery"].sudo().with_company(self.config.company_id)
        retry_before = fields.Datetime.now() - timedelta(hours=1)
        deliveries = Delivery.search([
            ("company_id", "=", self.config.company_id.id),
            ("flow_type", "=", "forward"),
            ("shipment_fees_present", "=", False),
            ("lifecycle_stage", "in", ["with_bosta", "delivered_to_customer", "terminated", "lost", "damaged"]),
            "|",
            ("financial_details_last_enriched_at", "=", False),
            ("financial_details_last_enriched_at", "<=", retry_before),
        ], order="financial_details_last_enriched_at asc, bosta_updated_at desc, id desc", limit=limit)
        result = {"attempted": 0, "updated": 0, "errors": 0, "stopped": False, "stop_status": False}
        for delivery in deliveries:
            result["attempted"] += 1
            try:
                with self.env.cr.savepoint():
                    persisted = self.persistence.enrich_delivery_from_details(self.extraction, delivery)
                    enriched = persisted["record"]
                    enriched.with_context(bosta_financial_enrichment_meta=True).write({
                        "financial_details_last_enriched_at": fields.Datetime.now(),
                        "financial_details_last_status": "success",
                    })
                    self.finance.process_delivery(enriched)
                    if persisted["action"] == "updated":
                        result["updated"] += 1
            except BostaApiAuthenticationError:
                delivery.with_context(bosta_financial_enrichment_meta=True).write({
                    "financial_details_last_enriched_at": fields.Datetime.now(),
                    "financial_details_last_status": "authentication_failed",
                })
                result["errors"] += 1
                result["stopped"] = True
                result["stop_status"] = "authentication_failed"
                break
            except BostaApiRateLimitError:
                delivery.with_context(bosta_financial_enrichment_meta=True).write({
                    "financial_details_last_enriched_at": fields.Datetime.now(),
                    "financial_details_last_status": "rate_limited",
                })
                result["errors"] += 1
                result["stopped"] = True
                result["stop_status"] = "rate_limited"
                break
            except (BostaApiError, BostaPersistenceError):
                delivery.with_context(bosta_financial_enrichment_meta=True).write({
                    "financial_details_last_enriched_at": fields.Datetime.now(),
                    "financial_details_last_status": "failed",
                })
                result["errors"] += 1
                continue
        return result
