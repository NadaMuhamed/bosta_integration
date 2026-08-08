"""Phase 9 operational contribution engine.

The engine intentionally does not create accounting, sale, purchase, customer,
or payment records.  It snapshots Phase 7 inventory products/costs and uses
Phase 8 applied restoration evidence for return cost credits.
"""

import math
from collections import defaultdict

from odoo import fields


_FINANCIAL_ENGINE_CONTEXT = "bosta_financial_engine"
_EPSILON = 1e-9
_REVIEW_STAGES = {"ambiguous"}


class BostaFinancialService:
    def __init__(self, env, config=False):
        self.env = env
        self.config = config

    def _company_context(self, company):
        allowed = list(self.env.context.get("allowed_company_ids") or self.env.user.company_ids.ids)
        if company.id not in allowed:
            allowed.append(company.id)
        return dict(
            self.env.context,
            allowed_company_ids=allowed,
            **{_FINANCIAL_ENGINE_CONTEXT: True},
        )

    def _models(self, company):
        ctx = self._company_context(company)
        return (
            self.env["bosta.delivery.financial"].sudo().with_context(ctx).with_company(company),
            self.env["bosta.delivery.financial.line"].sudo().with_context(ctx).with_company(company),
        )

    def _adjustment_model(self, company):
        ctx = self._company_context(company)
        return self.env["bosta.financial.adjustment"].sudo().with_context(ctx).with_company(company)

    def _get_or_create_financial(self, delivery):
        Financial, _Line = self._models(delivery.company_id)
        financial = Financial.search([
            ("company_id", "=", delivery.company_id.id),
            ("delivery_id", "=", delivery.id),
        ], limit=1)
        if financial:
            return financial
        return Financial.create({
            "company_id": delivery.company_id.id,
            "delivery_id": delivery.id,
            "currency_id": delivery.company_id.currency_id.id,
            "financial_status": "not_ready",
            "revenue_source": "not_available",
            "logistics_cost_source": "unavailable",
            "logistics_cost_status": "unavailable",
            "return_fee_source": "not_available",
            "compensation_source": "not_applicable",
            "rule_code": "financial_snapshot_created",
        })

    @staticmethod
    def _inventory_effect(delivery):
        return delivery.env["bosta.inventory.effect"].sudo().with_company(delivery.company_id).search([
            ("company_id", "=", delivery.company_id.id),
            ("delivery_id", "=", delivery.id),
        ], limit=1)

    @staticmethod
    def _valid_cost(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        )

    def _ensure_cost_lines(self, financial):
        delivery = financial.delivery_id
        effect = self._inventory_effect(delivery)
        if not effect or effect.status not in ("outbound_applied", "delivered_finalized"):
            return effect
        Financial, Line = self._models(delivery.company_id)
        existing_keys = {
            (line.original_inventory_effect_line_id.id, line.role): line
            for line in financial.line_ids
        }
        now = fields.Datetime.now()
        for inv_line in effect.line_ids:
            roles = [("main", inv_line.main_product_id, inv_line.main_quantity)]
            if inv_line.tester_product_id and inv_line.tester_quantity > _EPSILON:
                roles.append(("tester", inv_line.tester_product_id, inv_line.tester_quantity))
            for role, product, quantity in roles:
                if quantity <= _EPSILON or (inv_line.id, role) in existing_keys:
                    continue
                raw_cost = product.with_company(delivery.company_id).standard_price
                if self._valid_cost(raw_cost):
                    unit_cost = financial.currency_id.round(raw_cost)
                    source = "product_standard_price"
                else:
                    unit_cost = 0.0
                    source = "unavailable"
                gross = financial.currency_id.round(unit_cost * quantity)
                Line.create({
                    "financial_id": financial.id,
                    "original_inventory_effect_line_id": inv_line.id,
                    "product_id": product.id,
                    "role": role,
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                    "gross_cost": gross,
                    "restored_quantity": 0.0,
                    "restored_cost_credit": 0.0,
                    "net_cost": gross,
                    "cost_source": source,
                    "cost_snapshotted_at": now,
                })
        return effect

    def _applied_restoration_lines(self, delivery):
        return delivery.env["bosta.return.restoration.effect.line"].sudo().with_company(delivery.company_id).search([
            ("company_id", "=", delivery.company_id.id),
            ("effect_id.original_delivery_id", "=", delivery.id),
            ("effect_id.status", "=", "applied"),
        ], order="id")

    def _process_finalized_adjustments(self, financial):
        """Record later Phase 8 restoration evidence without rewriting finalized history."""
        Adjustment = self._adjustment_model(financial.company_id)
        rows = self._applied_restoration_lines(financial.delivery_id)
        if not rows:
            return financial

        lines_by_key = {
            (line.original_inventory_effect_line_id.id, line.role): line
            for line in financial.line_ids
        }
        existing = Adjustment.search([
            ("company_id", "=", financial.company_id.id),
            ("financial_id", "=", financial.id),
        ])
        existing_by_restoration = {
            adjustment.restoration_effect_line_id.id: adjustment for adjustment in existing
        }
        adjusted_qty_by_line = defaultdict(float)
        for adjustment in existing:
            adjusted_qty_by_line[adjustment.financial_line_id.id] += adjustment.quantity

        # Quantities already present on finalized cost lines are part of the immutable
        # base snapshot. Consume that baseline against the oldest Phase 8 evidence,
        # then create adjustments only for authoritative restoration beyond it.
        baseline_remaining = {
            line.id: min(max(line.restored_quantity, 0.0), line.quantity)
            for line in financial.line_ids
        }
        now = fields.Datetime.now()
        for row in rows:
            key = (row.original_inventory_effect_line_id.id, row.role)
            line = lines_by_key.get(key)
            if not line:
                continue

            row_qty = row.quantity
            baseline = baseline_remaining.get(line.id, 0.0)
            if baseline > _EPSILON:
                absorbed = min(baseline, row_qty)
                baseline_remaining[line.id] = baseline - absorbed
                row_qty -= absorbed

            if row.id in existing_by_restoration or row_qty <= _EPSILON:
                continue

            # Phase 8 customer returns restore MAIN only. Enforce that rule again
            # at the finance boundary so TESTER always remains consumed.
            return_type = row.effect_id.return_type
            if return_type == "post_delivery_customer_return" and row.role != "main":
                continue

            credited_qty = line.restored_quantity + adjusted_qty_by_line[line.id]
            remaining_capacity = max(line.quantity - credited_qty, 0.0)
            if remaining_capacity <= _EPSILON:
                continue
            credit_qty = min(row_qty, remaining_capacity)
            if credit_qty <= _EPSILON:
                continue

            amount = financial.currency_id.round(line.unit_cost * credit_qty)
            adjustment = Adjustment.create({
                "company_id": financial.company_id.id,
                "financial_id": financial.id,
                "financial_line_id": line.id,
                "restoration_effect_line_id": row.id,
                "adjustment_type": "inventory_credit",
                "source": "phase8_restoration",
                "return_type": return_type,
                "role": row.role,
                "quantity": credit_qty,
                "unit_cost": line.unit_cost,
                "amount": amount,
                "rule_code": "post_finalize_restoration_credit",
                "created_at": now,
            })
            existing_by_restoration[row.id] = adjustment
            adjusted_qty_by_line[line.id] += credit_qty

        return financial

    def _restoration_quantities(self, delivery):
        rows = self._applied_restoration_lines(delivery)
        quantities = defaultdict(float)
        for row in rows:
            key = (row.original_inventory_effect_line_id.id, row.role)
            quantities[key] += row.quantity
        return quantities

    def _apply_restoration_credits(self, financial):
        quantities = self._restoration_quantities(financial.delivery_id)
        review = False
        lines_by_key = {
            (line.original_inventory_effect_line_id.id, line.role): line
            for line in financial.line_ids
        }
        for key, restored_qty in quantities.items():
            line = lines_by_key.get(key)
            if not line:
                review = True
                continue
            if restored_qty > line.quantity + _EPSILON:
                review = True
                restored_qty = line.quantity
            credit = financial.currency_id.round(line.unit_cost * restored_qty)
            net_cost = financial.currency_id.round(line.gross_cost - credit)
            if (
                abs(line.restored_quantity - restored_qty) > _EPSILON
                or not financial.currency_id.is_zero(line.restored_cost_credit - credit)
                or not financial.currency_id.is_zero(line.net_cost - net_cost)
            ):
                line.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
                    "restored_quantity": restored_qty,
                    "restored_cost_credit": credit,
                    "net_cost": net_cost,
                })
        # If a previously provisional return disappears from the evidence set,
        # remove only the provisional credit before finalization.
        for key, line in lines_by_key.items():
            if key not in quantities and line.restored_quantity > _EPSILON:
                line.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
                    "restored_quantity": 0.0,
                    "restored_cost_credit": 0.0,
                    "net_cost": line.gross_cost,
                })
        return review

    def _sync_logistics_fee(self, financial):
        delivery = financial.delivery_id
        currency = financial.currency_id
        code = (delivery.pricing_currency_code or "").strip().upper()
        currency_ok = bool(delivery.pricing_currency_code_present and code == (currency.name or "").upper())

        if financial.logistics_cost_source == "manager_confirmed":
            if delivery.shipment_fees_present and currency_ok:
                authoritative = currency.round(delivery.shipment_fees)
                if not currency.is_zero(authoritative - financial.logistics_cost_amount):
                    financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
                        "logistics_cost_status": "review_required",
                        "safe_review_reason": "conflicting_logistics_sources",
                        "rule_code": "logistics_conflict",
                    })
                    return "review"
            return "authoritative"

        if delivery.shipment_fees_present:
            values = {
                "logistics_cost_amount": currency.round(delivery.shipment_fees),
                "logistics_cost_source": "shipment_fees",
            }
            if not delivery.pricing_currency_code_present:
                values.update({
                    "logistics_cost_status": "partial",
                    "safe_review_reason": "logistics_currency_missing",
                    "rule_code": "fee_currency_missing",
                })
                financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(values)
                return "partial"
            if not currency_ok:
                values.update({
                    "logistics_cost_status": "review_required",
                    "safe_review_reason": "logistics_currency_conflict",
                    "rule_code": "fee_currency_conflict",
                })
                financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(values)
                return "review"
            values.update({
                "logistics_cost_status": "authoritative",
                "safe_review_reason": False,
            })
            financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(values)
            return "authoritative"

        component_present = any([
            delivery.shipping_fee_present,
            delivery.opening_package_fee_present,
            delivery.bosta_material_fee_present,
            delivery.bundle_discount_present,
            delivery.price_before_vat_present,
            delivery.price_after_vat_present,
        ])
        values = {
            "logistics_cost_amount": 0.0,
            "logistics_cost_source": "explicit_pricing_components" if component_present else "unavailable",
            "logistics_cost_status": "partial" if component_present else "unavailable",
            "safe_review_reason": "logistics_total_not_authoritative" if component_present else "logistics_cost_missing",
            "rule_code": "fee_components_partial" if component_present else "fee_missing",
        }
        financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(values)
        return values["logistics_cost_status"]

    def _sync_return_fee_and_compensation(self, financial):
        delivery = financial.delivery_id
        ReturnCase = delivery.env["bosta.return.case"].sudo().with_company(delivery.company_id)
        has_customer_return = bool(ReturnCase.search_count([
            ("company_id", "=", delivery.company_id.id),
            ("original_delivery_id", "=", delivery.id),
            ("return_type", "=", "post_delivery_customer_return"),
        ]))
        vals = {}
        if financial.return_fee_source not in ("manager_confirmed", "bosta_authoritative"):
            if has_customer_return:
                vals.update({"return_fee_amount": 0.0, "return_fee_source": "not_available"})
            else:
                vals.update({"return_fee_amount": 0.0, "return_fee_source": "not_applicable"})

        if financial.compensation_source not in ("manager_confirmed", "bosta_authoritative"):
            if delivery.lifecycle_stage in ("lost", "damaged"):
                vals.update({"compensation_amount": 0.0, "compensation_source": "not_available"})
            else:
                vals.update({"compensation_amount": 0.0, "compensation_source": "not_applicable"})
        if vals:
            financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(vals)
        return has_customer_return

    def _aggregate_costs(self, financial):
        currency = financial.currency_id
        main = currency.round(sum(line.gross_cost for line in financial.line_ids if line.role == "main"))
        tester = currency.round(sum(line.gross_cost for line in financial.line_ids if line.role == "tester"))
        gross = currency.round(main + tester)
        restored_main = currency.round(sum(line.restored_cost_credit for line in financial.line_ids if line.role == "main"))
        restored_tester = currency.round(sum(line.restored_cost_credit for line in financial.line_ids if line.role == "tester"))
        net = currency.round(gross - restored_main - restored_tester)
        return {
            "main_cogs_amount": main,
            "tester_cogs_amount": tester,
            "gross_cogs_amount": gross,
            "restored_main_cost_credit": restored_main,
            "restored_tester_cost_credit": restored_tester,
            "net_cogs_amount": net,
        }

    def _status_and_reason(self, financial, effect, restoration_review, has_customer_return):
        delivery = financial.delivery_id
        if restoration_review:
            return "review_required", "restoration_credit_exceeds_snapshot", "restoration_credit_review"
        if delivery.lifecycle_stage in _REVIEW_STAGES or delivery.lifecycle_ambiguous:
            return "review_required", "ambiguous_or_terminal_inventory_outcome", "terminal_financial_review"
        if delivery.lifecycle_stage == "terminated" and effect and effect.status in ("outbound_applied", "delivered_finalized"):
            return "review_required", "terminated_after_inventory_departure", "terminated_after_pickup_review"

        outbound = bool(effect and effect.status in ("outbound_applied", "delivered_finalized"))
        has_restoration_evidence = any(
            line.restored_quantity > _EPSILON for line in financial.line_ids
        )

        # A forward parcel still travelling with Bosta is not financially final.
        # The one safe exception is a completed Phase 8 RTO whose applied stock
        # restoration is visible on the original snapshot lines.
        if delivery.lifecycle_stage == "with_bosta" and not has_restoration_evidence:
            return "not_ready", "forward_delivery_still_with_bosta", "financial_not_ready"
        if delivery.lifecycle_stage in ("unknown", "pre_pickup"):
            if outbound:
                return "review_required", "inventory_departure_conflicts_with_lifecycle", "lifecycle_stock_conflict"
            return "not_ready", "inventory_not_financially_final", "financial_not_ready"

        if not outbound:
            if delivery.lifecycle_stage == "terminated":
                # No Phase 7 outbound evidence means no inventory COGS was consumed.
                pass
            elif delivery.lifecycle_stage == "delivered_to_customer":
                return "review_required", "delivered_without_outbound_inventory_evidence", "missing_outbound_evidence"

        if any(line.cost_source == "unavailable" for line in financial.line_ids):
            return "incomplete", "product_cost_missing", "cost_incomplete"
        if financial.revenue_source == "not_available":
            return "incomplete", "recognized_revenue_missing", "revenue_missing"
        if financial.logistics_cost_status == "review_required":
            return "review_required", financial.safe_review_reason or "logistics_review_required", "logistics_review"
        if financial.logistics_cost_status != "authoritative":
            return "incomplete", financial.safe_review_reason or "logistics_cost_missing", "logistics_incomplete"
        if has_customer_return and financial.return_fee_source == "not_available":
            return "incomplete", "customer_return_fee_unknown", "return_fee_missing"
        if delivery.lifecycle_stage in ("lost", "damaged") and financial.compensation_source == "not_available":
            return "review_required", "compensation_unknown", "compensation_review"
        return "ready", False, "financial_ready"

    def process_delivery(self, delivery):
        delivery.ensure_one()
        if delivery.flow_type != "forward":
            original = delivery.original_delivery_id
            if not original or original.company_id != delivery.company_id or original.flow_type != "forward":
                return False
            delivery = original

        financial = self._get_or_create_financial(delivery)
        if financial.financial_status == "finalized":
            return self._process_finalized_adjustments(financial)

        effect = self._ensure_cost_lines(financial)
        restoration_review = self._apply_restoration_credits(financial)
        self._sync_logistics_fee(financial)
        has_customer_return = self._sync_return_fee_and_compensation(financial)
        financial.invalidate_recordset()

        aggregate = self._aggregate_costs(financial)
        financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(aggregate)
        financial.invalidate_recordset()

        status, reason, rule = self._status_and_reason(
            financial, effect, restoration_review, has_customer_return
        )
        if status != "ready":
            financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
                "financial_status": status,
                "contribution_profit": 0.0,
                "calculated_at": False,
                "safe_review_reason": reason or False,
                "rule_code": rule,
            })
            return financial

        currency = financial.currency_id
        contribution = currency.round(
            financial.recognized_revenue_amount
            - financial.net_cogs_amount
            - financial.logistics_cost_amount
            - financial.return_fee_amount
            + financial.compensation_amount
        )
        financial.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
            "financial_status": "calculated",
            "contribution_profit": contribution,
            "calculated_at": fields.Datetime.now(),
            "safe_review_reason": False,
            "rule_code": "contribution_calculated",
        })
        return financial

    def process_company_pending(self, company, *, limit=200):
        Delivery = self.env["bosta.delivery"].sudo().with_company(company)
        deliveries = Delivery.search([
            ("company_id", "=", company.id),
            ("flow_type", "=", "forward"),
            ("lifecycle_stage", "in", [
                "delivered_to_customer", "terminated", "lost", "damaged", "ambiguous"
            ]),
        ], limit=limit)
        counts = {"processed": 0, "calculated": 0, "incomplete": 0, "review_required": 0}
        for delivery in deliveries:
            with self.env.cr.savepoint():
                result = self.process_delivery(delivery)
            if not result:
                continue
            counts["processed"] += 1
            if result.financial_status in counts:
                counts[result.financial_status] += 1
        return counts
