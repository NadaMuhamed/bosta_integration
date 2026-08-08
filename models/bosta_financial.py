import math

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


_FINANCIAL_ENGINE_CONTEXT = "bosta_financial_engine"

FINANCIAL_STATUS_SELECTION = [
    ("not_ready", "Not Ready"),
    ("incomplete", "Incomplete"),
    ("ready", "Ready"),
    ("calculated", "Calculated"),
    ("finalized", "Finalized"),
    ("review_required", "Review Required"),
]
REVENUE_SOURCE_SELECTION = [
    ("not_available", "Not Available"),
    ("explicit_source", "Explicit Source"),
    ("manager_confirmed", "Manager Confirmed"),
    ("cod_confirmed", "COD Confirmed"),
    ("future_order_source", "Future Order Source"),
]
LOGISTICS_SOURCE_SELECTION = [
    ("unavailable", "Unavailable"),
    ("shipment_fees", "Bosta Shipment Fees"),
    ("explicit_pricing_components", "Pricing Components (Partial)"),
    ("manager_confirmed", "Manager Confirmed"),
    ("settlement", "Settlement"),
]
LOGISTICS_STATUS_SELECTION = [
    ("unavailable", "Unavailable"),
    ("partial", "Partial"),
    ("authoritative", "Authoritative"),
    ("review_required", "Review Required"),
]
RETURN_FEE_SOURCE_SELECTION = [
    ("not_available", "Not Available"),
    ("not_applicable", "Not Applicable"),
    ("manager_confirmed", "Manager Confirmed"),
    ("bosta_authoritative", "Bosta Authoritative"),
]
COMPENSATION_SOURCE_SELECTION = [
    ("not_available", "Not Available"),
    ("not_applicable", "Not Applicable"),
    ("manager_confirmed", "Manager Confirmed"),
    ("bosta_authoritative", "Bosta Authoritative"),
]
COST_SOURCE_SELECTION = [
    ("product_standard_price", "Product Standard Price"),
    ("explicit_override", "Explicit Override"),
    ("unavailable", "Unavailable"),
]
ROLE_SELECTION = [("main", "MAIN"), ("tester", "TESTER")]
ADJUSTMENT_TYPE_SELECTION = [("inventory_credit", "Inventory Cost Credit")]
ADJUSTMENT_SOURCE_SELECTION = [("phase8_restoration", "Phase 8 Applied Restoration")]
RETURN_TYPE_SELECTION = [
    ("pre_delivery_return", "Pre-delivery Return / RTO"),
    ("post_delivery_customer_return", "Post-delivery Customer Return"),
]


class BostaDeliveryFinancial(models.Model):
    _name = "bosta.delivery.financial"
    _description = "Bosta Delivery Operational Financial Snapshot"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    delivery_id = fields.Many2one(
        "bosta.delivery",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
        domain="[('flow_type', '=', 'forward')]",
    )
    financial_status = fields.Selection(
        FINANCIAL_STATUS_SELECTION,
        required=True,
        default="not_ready",
        readonly=True,
        index=True,
        copy=False,
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, readonly=True, ondelete="restrict"
    )

    recognized_revenue_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    revenue_source = fields.Selection(
        REVENUE_SOURCE_SELECTION,
        required=True,
        default="not_available",
        readonly=True,
        index=True,
    )
    revenue_confirmed_at = fields.Datetime(readonly=True, copy=False)
    revenue_confirmed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    revenue_override_reason = fields.Char(readonly=True, copy=False)

    main_cogs_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    tester_cogs_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    gross_cogs_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    restored_main_cost_credit = fields.Monetary(currency_field="currency_id", readonly=True)
    restored_tester_cost_credit = fields.Monetary(currency_field="currency_id", readonly=True)
    net_cogs_amount = fields.Monetary(currency_field="currency_id", readonly=True)

    logistics_cost_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    logistics_cost_source = fields.Selection(
        LOGISTICS_SOURCE_SELECTION,
        required=True,
        default="unavailable",
        readonly=True,
    )
    logistics_cost_status = fields.Selection(
        LOGISTICS_STATUS_SELECTION,
        required=True,
        default="unavailable",
        readonly=True,
        index=True,
    )
    logistics_confirmed_at = fields.Datetime(readonly=True, copy=False)
    logistics_confirmed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    logistics_override_reason = fields.Char(readonly=True, copy=False)

    return_fee_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    return_fee_source = fields.Selection(
        RETURN_FEE_SOURCE_SELECTION,
        required=True,
        default="not_available",
        readonly=True,
    )
    return_fee_confirmed_at = fields.Datetime(readonly=True, copy=False)
    return_fee_confirmed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    return_fee_override_reason = fields.Char(readonly=True, copy=False)

    compensation_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    compensation_source = fields.Selection(
        COMPENSATION_SOURCE_SELECTION,
        required=True,
        default="not_applicable",
        readonly=True,
    )
    compensation_confirmed_at = fields.Datetime(readonly=True, copy=False)
    compensation_confirmed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    compensation_override_reason = fields.Char(readonly=True, copy=False)

    contribution_profit = fields.Monetary(currency_field="currency_id", readonly=True)
    calculated_at = fields.Datetime(readonly=True, copy=False)
    finalized_at = fields.Datetime(readonly=True, copy=False)
    finalized_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    rule_code = fields.Char(readonly=True, index=True, copy=False)
    safe_review_reason = fields.Char(readonly=True, copy=False)

    line_ids = fields.One2many(
        "bosta.delivery.financial.line", "financial_id", string="Cost Snapshots", readonly=True
    )
    adjustment_ids = fields.One2many(
        "bosta.financial.adjustment", "financial_id", string="Post-finalization Adjustments", readonly=True
    )
    post_finalize_inventory_credit = fields.Monetary(
        currency_field="currency_id", compute="_compute_adjustment_totals", readonly=True
    )
    contribution_after_adjustments = fields.Monetary(
        currency_field="currency_id", compute="_compute_adjustment_totals", readonly=True
    )

    # Manager staging inputs are deliberately separate from authoritative values.
    manual_revenue_input = fields.Monetary(currency_field="currency_id", copy=False)
    manual_logistics_input = fields.Monetary(currency_field="currency_id", copy=False)
    manual_return_fee_input = fields.Monetary(currency_field="currency_id", copy=False)
    manual_compensation_input = fields.Monetary(currency_field="currency_id", copy=False)
    manual_override_reason = fields.Char(copy=False)

    _sql_constraints = [
        (
            "bosta_financial_company_delivery_unique",
            "unique(company_id, delivery_id)",
            "A forward Bosta delivery can have only one financial snapshot per company.",
        ),
    ]

    @api.depends("adjustment_ids", "contribution_profit")
    def _compute_adjustment_totals(self):
        for record in self:
            credit = record.currency_id.round(sum(record.adjustment_ids.mapped("amount")))
            record.post_finalize_inventory_credit = credit
            record.contribution_after_adjustments = record.currency_id.round(
                record.contribution_profit + credit
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            raise AccessError(_("Financial snapshots are created only by the Bosta financial engine."))
        records = super().create(vals_list)
        # original_financial_id intentionally depends only on original_delivery_id
        # so registry loading remains safe when upgrading a Phase 8 database.
        # Creating the financial later does not touch that dependency, so clear
        # the non-stored compute cache for already-linked return cases.
        return_cases = self.env["bosta.return.case"].search([
            ("company_id", "in", records.company_id.ids),
            ("original_delivery_id", "in", records.delivery_id.ids),
        ])
        if return_cases:
            return_cases.invalidate_recordset(["original_financial_id"])
        return records

    def write(self, vals):
        if self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            if any(record.financial_status == "finalized" for record in self):
                raise ValidationError(_("Finalized financial base snapshots are immutable; use adjustments."))
            return super().write(vals)
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager may edit financial review inputs."))
        allowed = {
            "manual_revenue_input",
            "manual_logistics_input",
            "manual_return_fee_input",
            "manual_compensation_input",
            "manual_override_reason",
        }
        if set(vals) - allowed:
            raise AccessError(_("Authoritative financial values can be changed only through reviewed manager actions."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            raise AccessError(_("Financial snapshots cannot be deleted manually."))
        if any(rec.financial_status == "finalized" for rec in self):
            raise ValidationError(_("Finalized financial snapshots cannot be deleted."))
        return super().unlink()

    def _ensure_manager(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager can perform this financial action."))
        self.check_access("write")

    def _engine_write(self, vals):
        return self.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write(vals)

    def _service_recalculate(self):
        from ..services.bosta_financial_service import BostaFinancialService

        config = self.env["bosta.integration.config"].sudo().with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
        ], order="id desc", limit=1)
        return BostaFinancialService(self.env, config or False).process_delivery(self.delivery_id)

    def _confirm_revenue_value(self, amount, source):
        self.ensure_one()
        if not math.isfinite(amount) or amount < 0:
            raise UserError(_("Recognized revenue must be a finite non-negative amount."))
        currency = self.currency_id
        if self.revenue_source != "not_available":
            if not currency.is_zero(self.recognized_revenue_amount - amount):
                self._engine_write({
                    "financial_status": "review_required",
                    "safe_review_reason": "conflicting_revenue_confirmation",
                    "rule_code": "revenue_conflict",
                })
                return self
            return self._service_recalculate()
        self._engine_write({
            "recognized_revenue_amount": currency.round(amount),
            "revenue_source": source,
            "revenue_confirmed_at": fields.Datetime.now(),
            "revenue_confirmed_by_id": self.env.user.id,
            "revenue_override_reason": self.manual_override_reason or "manager_revenue_confirmation",
            "safe_review_reason": False,
        })
        return self._service_recalculate()

    def action_confirm_revenue(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        return self._confirm_revenue_value(self.manual_revenue_input, "manager_confirmed")

    def action_confirm_cod_as_revenue(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        delivery = self.delivery_id
        if not delivery.cod_amount_present:
            raise UserError(_("COD is not explicitly present for this delivery."))
        return self._confirm_revenue_value(delivery.cod_amount, "cod_confirmed")

    def action_confirm_logistics_cost(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        amount = self.manual_logistics_input
        if not math.isfinite(amount) or amount < 0:
            raise UserError(_("Logistics cost must be a finite non-negative amount."))
        rounded = self.currency_id.round(amount)
        if self.logistics_cost_source == "manager_confirmed" and self.currency_id.is_zero(
            self.logistics_cost_amount - rounded
        ):
            return self._service_recalculate()
        self._engine_write({
            "logistics_cost_amount": rounded,
            "logistics_cost_source": "manager_confirmed",
            "logistics_cost_status": "authoritative",
            "logistics_confirmed_at": fields.Datetime.now(),
            "logistics_confirmed_by_id": self.env.user.id,
            "logistics_override_reason": self.manual_override_reason or "manager_logistics_confirmation",
            "safe_review_reason": False,
        })
        return self._service_recalculate()

    def action_confirm_return_fee(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        amount = self.manual_return_fee_input
        if not math.isfinite(amount) or amount < 0:
            raise UserError(_("Return fee must be a finite non-negative amount."))
        self._engine_write({
            "return_fee_amount": self.currency_id.round(amount),
            "return_fee_source": "manager_confirmed",
            "return_fee_confirmed_at": fields.Datetime.now(),
            "return_fee_confirmed_by_id": self.env.user.id,
            "return_fee_override_reason": self.manual_override_reason or "manager_return_fee_confirmation",
            "safe_review_reason": False,
        })
        return self._service_recalculate()

    def action_confirm_compensation(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        amount = self.manual_compensation_input
        if not math.isfinite(amount) or amount < 0:
            raise UserError(_("Compensation must be a finite non-negative amount."))
        self._engine_write({
            "compensation_amount": self.currency_id.round(amount),
            "compensation_source": "manager_confirmed",
            "compensation_confirmed_at": fields.Datetime.now(),
            "compensation_confirmed_by_id": self.env.user.id,
            "compensation_override_reason": self.manual_override_reason or "manager_compensation_confirmation",
            "safe_review_reason": False,
        })
        return self._service_recalculate()

    def action_recalculate(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        return self._service_recalculate()

    def action_finalize(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            return True
        if self.financial_status != "calculated":
            raise UserError(_("Only a complete calculated result can be finalized."))
        self._engine_write({
            "financial_status": "finalized",
            "finalized_at": fields.Datetime.now(),
            "finalized_by_id": self.env.user.id,
            "rule_code": "financial_finalized",
        })
        return True

    def action_mark_review_required(self):
        self._ensure_manager()
        if self.financial_status == "finalized":
            raise UserError(_("Finalized financial history must not be rewritten."))
        self._engine_write({
            "financial_status": "review_required",
            "safe_review_reason": "manager_review_required",
            "rule_code": "manager_review_required",
        })
        return True


class BostaDeliveryFinancialLine(models.Model):
    _name = "bosta.delivery.financial.line"
    _description = "Bosta Delivery Immutable Cost Snapshot Line"
    _order = "id"
    _check_company_auto = True

    financial_id = fields.Many2one(
        "bosta.delivery.financial", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="financial_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="financial_id.currency_id", store=True, readonly=True)
    original_inventory_effect_line_id = fields.Many2one(
        "bosta.inventory.effect.line",
        required=True,
        ondelete="restrict",
        check_company=True,
        readonly=True,
        index=True,
    )
    product_id = fields.Many2one(
        "product.product", required=True, ondelete="restrict", check_company=True, readonly=True
    )
    role = fields.Selection(ROLE_SELECTION, required=True, readonly=True, index=True)
    quantity = fields.Float(required=True, readonly=True)
    unit_cost = fields.Monetary(currency_field="currency_id", readonly=True)
    gross_cost = fields.Monetary(currency_field="currency_id", readonly=True)
    cost_source = fields.Selection(
        COST_SOURCE_SELECTION, required=True, default="unavailable", readonly=True
    )
    restored_quantity = fields.Float(readonly=True)
    restored_cost_credit = fields.Monetary(currency_field="currency_id", readonly=True)
    net_cost = fields.Monetary(currency_field="currency_id", readonly=True)
    cost_snapshotted_at = fields.Datetime(readonly=True, copy=False)
    cost_overridden_at = fields.Datetime(readonly=True, copy=False)
    cost_overridden_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    cost_override_reason = fields.Char(readonly=True, copy=False)

    manual_unit_cost_input = fields.Monetary(currency_field="currency_id", copy=False)
    manual_override_reason = fields.Char(copy=False)

    _sql_constraints = [
        (
            "bosta_financial_line_unique",
            "unique(financial_id, original_inventory_effect_line_id, role)",
            "Each historical inventory role can have only one cost snapshot line.",
        ),
        (
            "bosta_financial_line_positive_qty",
            "CHECK(quantity > 0)",
            "Financial cost quantity must be positive.",
        ),
        (
            "bosta_financial_line_restored_nonnegative",
            "CHECK(restored_quantity >= 0)",
            "Restored financial quantity cannot be negative.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            raise AccessError(_("Financial cost lines are created only by the Bosta financial engine."))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            if any(line.financial_id.financial_status == "finalized" for line in self):
                immutable = set(vals) & {
                    "product_id", "role", "quantity", "unit_cost", "gross_cost", "cost_source",
                    "restored_quantity", "restored_cost_credit", "net_cost",
                }
                if immutable:
                    raise ValidationError(_("Finalized historical cost snapshots are immutable."))
            return super().write(vals)
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager may edit cost override inputs."))
        if set(vals) - {"manual_unit_cost_input", "manual_override_reason"}:
            raise AccessError(_("Historical cost values can be changed only by the explicit override action."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            raise AccessError(_("Financial cost lines cannot be deleted manually."))
        if any(line.financial_id.financial_status == "finalized" for line in self):
            raise ValidationError(_("Finalized financial cost lines cannot be deleted."))
        return super().unlink()

    def action_confirm_cost_override(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager can override a cost snapshot."))
        if self.financial_id.financial_status == "finalized":
            raise UserError(_("Finalized historical cost snapshots are immutable."))
        amount = self.manual_unit_cost_input
        if not math.isfinite(amount) or amount < 0:
            raise UserError(_("Unit cost override must be a finite non-negative amount."))
        currency = self.currency_id
        unit_cost = currency.round(amount)
        gross = currency.round(unit_cost * self.quantity)
        restored_credit = currency.round(unit_cost * self.restored_quantity)
        self.with_context(**{_FINANCIAL_ENGINE_CONTEXT: True}).write({
            "unit_cost": unit_cost,
            "gross_cost": gross,
            "restored_cost_credit": restored_credit,
            "net_cost": currency.round(gross - restored_credit),
            "cost_source": "explicit_override",
            "cost_overridden_at": fields.Datetime.now(),
            "cost_overridden_by_id": self.env.user.id,
            "cost_override_reason": self.manual_override_reason or "manager_cost_override",
        })
        return self.financial_id.action_recalculate()


class BostaFinancialAdjustment(models.Model):
    _name = "bosta.financial.adjustment"
    _description = "Bosta Immutable Post-finalization Financial Adjustment"
    _order = "id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    financial_id = fields.Many2one(
        "bosta.delivery.financial", required=True, index=True, ondelete="restrict", check_company=True
    )
    financial_line_id = fields.Many2one(
        "bosta.delivery.financial.line", required=True, index=True, ondelete="restrict", check_company=True
    )
    restoration_effect_line_id = fields.Many2one(
        "bosta.return.restoration.effect.line", required=True, index=True, ondelete="restrict", check_company=True
    )
    currency_id = fields.Many2one(related="financial_id.currency_id", store=True, readonly=True)
    adjustment_type = fields.Selection(ADJUSTMENT_TYPE_SELECTION, required=True, readonly=True)
    source = fields.Selection(ADJUSTMENT_SOURCE_SELECTION, required=True, readonly=True)
    return_type = fields.Selection(RETURN_TYPE_SELECTION, required=True, readonly=True)
    role = fields.Selection(ROLE_SELECTION, required=True, readonly=True, index=True)
    quantity = fields.Float(required=True, readonly=True)
    unit_cost = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    amount = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    rule_code = fields.Char(required=True, readonly=True, index=True)
    created_at = fields.Datetime(required=True, readonly=True, copy=False)

    _sql_constraints = [
        (
            "bosta_financial_adjustment_restoration_unique",
            "unique(financial_id, restoration_effect_line_id)",
            "Each applied Phase 8 restoration line can create at most one financial adjustment.",
        ),
        (
            "bosta_financial_adjustment_positive_qty",
            "CHECK(quantity > 0)",
            "Financial adjustment quantity must be positive.",
        ),
        (
            "bosta_financial_adjustment_nonnegative_amount",
            "CHECK(amount >= 0)",
            "Financial adjustment amount cannot be negative.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_FINANCIAL_ENGINE_CONTEXT):
            raise AccessError(_("Financial adjustments are created only by the Bosta financial engine."))
        records = super().create(vals_list)
        records._check_adjustment_evidence()
        return records

    @api.constrains(
        "company_id", "financial_id", "financial_line_id",
        "restoration_effect_line_id", "role", "return_type"
    )
    def _check_adjustment_evidence(self):
        for adjustment in self:
            financial = adjustment.financial_id
            line = adjustment.financial_line_id
            restoration = adjustment.restoration_effect_line_id
            if financial.financial_status != "finalized":
                raise ValidationError(_("Post-finalization adjustments require a finalized base snapshot."))
            if not (
                adjustment.company_id == financial.company_id
                and line.company_id == financial.company_id
                and restoration.company_id == financial.company_id
            ):
                raise ValidationError(_("Financial adjustment evidence must remain within one company."))
            if line.financial_id != financial:
                raise ValidationError(_("Financial adjustment line must belong to its finalized snapshot."))
            if restoration.effect_id.original_delivery_id != financial.delivery_id:
                raise ValidationError(_("Financial adjustment must use the safely linked original delivery."))
            if restoration.effect_id.status != "applied":
                raise ValidationError(_("Financial adjustment requires applied Phase 8 restoration evidence."))
            if restoration.original_inventory_effect_line_id != line.original_inventory_effect_line_id:
                raise ValidationError(_("Financial adjustment restoration evidence does not match the historical cost line."))
            if restoration.role != adjustment.role or line.role != adjustment.role:
                raise ValidationError(_("Financial adjustment role does not match restoration evidence."))
            if restoration.effect_id.return_type != adjustment.return_type:
                raise ValidationError(_("Financial adjustment return type does not match restoration evidence."))
            if adjustment.return_type == "post_delivery_customer_return" and adjustment.role != "main":
                raise ValidationError(_("Customer returns may credit MAIN cost only; TESTER remains consumed."))

    def write(self, vals):
        raise ValidationError(_("Financial adjustments are immutable."))

    def unlink(self):
        raise ValidationError(_("Financial adjustments are immutable."))
