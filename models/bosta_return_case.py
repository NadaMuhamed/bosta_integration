from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


_RETURN_ENGINE_CONTEXT = "bosta_return_engine"

RETURN_TYPE_SELECTION = [
    ("pre_delivery_return", "Pre-delivery Return / RTO"),
    ("post_delivery_customer_return", "Post-delivery Customer Return"),
]

RETURN_CASE_STATE_SELECTION = [
    ("pending_link", "Pending Original Link"),
    ("awaiting_physical_return", "Awaiting Physical Return"),
    ("awaiting_inspection", "Awaiting Inspection"),
    ("ready_to_restore", "Ready to Restore"),
    ("restored", "Restored"),
    ("rejected", "Rejected"),
    ("blocked", "Blocked"),
    ("review_required", "Review Required"),
]

INSPECTION_STATE_SELECTION = [
    ("not_required", "Not Required"),
    ("pending", "Pending"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
]

RESTORATION_STATUS_SELECTION = [
    ("pending", "Pending"),
    ("applied", "Applied"),
    ("blocked", "Blocked"),
]

RESTORATION_ROLE_SELECTION = [
    ("main", "MAIN"),
    ("tester", "TESTER"),
]


class BostaReturnCase(models.Model):
    _name = "bosta.return.case"
    _description = "Bosta Return Case"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade"
    )
    return_delivery_id = fields.Many2one(
        "bosta.delivery",
        string="Return Delivery",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    original_delivery_id = fields.Many2one(
        "bosta.delivery",
        string="Original Delivery",
        index=True,
        ondelete="set null",
        check_company=True,
        readonly=True,
    )
    link_candidate_delivery_id = fields.Many2one(
        "bosta.delivery",
        string="Original Link Candidate",
        check_company=True,
        ondelete="set null",
        domain="[('company_id', '=', company_id), ('flow_type', '=', 'forward'), ('id', '!=', return_delivery_id)]",
        help="Manager-selected candidate only. Bosta businessReference is never used as authoritative identity.",
    )
    return_type = fields.Selection(
        RETURN_TYPE_SELECTION, required=True, index=True, readonly=True
    )
    state = fields.Selection(
        RETURN_CASE_STATE_SELECTION,
        required=True,
        default="pending_link",
        index=True,
        readonly=True,
    )
    inspection_state = fields.Selection(
        INSPECTION_STATE_SELECTION,
        required=True,
        default="not_required",
        index=True,
        readonly=True,
    )
    reason_code = fields.Char(
        string="Review / Rule Code",
        readonly=True,
        index=True,
        help="Fixed non-PII status/rule code for return audit.",
    )
    restored_at = fields.Datetime(readonly=True, copy=False)
    restoration_effect_id = fields.Many2one(
        "bosta.return.restoration.effect",
        string="Restoration Effect",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    restoration_picking_id = fields.Many2one(
        related="restoration_effect_id.picking_id",
        string="Restoration Picking",
        readonly=True,
    )
    return_line_ids = fields.One2many(
        "bosta.return.case.line",
        "case_id",
        string="Customer Return Quantities",
        readonly=False,
    )

    _sql_constraints = [
        (
            "bosta_return_case_delivery_unique",
            "unique(company_id, return_delivery_id)",
            "A Bosta return delivery can have only one return case per company.",
        ),
    ]

    @api.model
    def _engine_allowed(self):
        return bool(self.env.context.get(_RETURN_ENGINE_CONTEXT))

    def _ensure_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager can manage return cases."))
        self.check_access("write")

    @api.model_create_multi
    def create(self, vals_list):
        if not self._engine_allowed():
            raise AccessError(_("Bosta return cases are created only by the return engine."))
        return super().create(vals_list)

    def write(self, vals):
        if self._engine_allowed():
            return super().write(vals)
        self._ensure_manager()
        allowed = {"link_candidate_delivery_id"}
        if set(vals) - allowed:
            raise AccessError(_("Return workflow state is maintained only by the return engine."))
        return super().write(vals)

    def unlink(self):
        if not self._engine_allowed():
            raise AccessError(_("Bosta return cases cannot be deleted manually."))
        if any(case.restoration_effect_id for case in self):
            raise ValidationError(_("A return case with restoration history cannot be deleted."))
        return super().unlink()

    def _notification(self, message, *, success=False):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Return"),
                "message": message,
                "type": "success" if success else "warning",
                "sticky": not success,
            },
        }

    def _service(self):
        self.ensure_one()
        config = self.env["bosta.integration.config"].sudo().with_company(self.company_id).search(
            [("company_id", "=", self.company_id.id)], limit=1
        )
        if not config:
            raise UserError(_("No Bosta integration configuration exists for this company."))
        from ..services.bosta_return_service import BostaReturnService

        return BostaReturnService(self.env, config)

    def action_link_original_delivery(self):
        self.ensure_one()
        self._ensure_manager()
        candidate = self.link_candidate_delivery_id
        if not candidate:
            raise UserError(_("Select an original forward delivery candidate first."))
        service = self._service()
        service.link_original(self, candidate)
        service.process_case(self)
        return self._notification(_("The original delivery link was validated and applied."), success=True)

    def action_unlink_original_delivery(self):
        self.ensure_one()
        self._ensure_manager()
        service = self._service()
        service.unlink_original(self)
        return self._notification(_("The original delivery link was removed."), success=True)

    def action_accept_returned_product(self):
        self.ensure_one()
        self._ensure_manager()
        result = self._service().accept_customer_return(self)
        if result.state == "restored":
            return self._notification(_("Returned MAIN inventory was restored exactly once."), success=True)
        return self._notification(_("The return was not restored; review the return case status and rule code."))

    def action_reject_returned_product(self):
        self.ensure_one()
        self._ensure_manager()
        result = self._service().reject_customer_return(self)
        return self._notification(
            _("Returned product inspection is rejected; no stock was restored."),
            success=result.inspection_state == "rejected",
        )

    def action_retry_restoration(self):
        self.ensure_one()
        self._ensure_manager()
        result = self._service().process_case(self)
        if result.state == "restored":
            return self._notification(_("Return restoration is applied exactly once."), success=True)
        return self._notification(_("Return restoration remains blocked/pending; review the rule code."))


class BostaReturnCaseLine(models.Model):
    _name = "bosta.return.case.line"
    _description = "Bosta Customer Return Quantity Line"
    _order = "id"
    _check_company_auto = True

    case_id = fields.Many2one(
        "bosta.return.case", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="case_id.company_id", store=True, index=True)
    original_inventory_effect_line_id = fields.Many2one(
        "bosta.inventory.effect.line",
        required=True,
        check_company=True,
        ondelete="restrict",
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product", required=True, check_company=True, ondelete="restrict", readonly=True
    )
    max_delivered_quantity = fields.Float(required=True, readonly=True)
    returned_quantity = fields.Float(
        string="Accepted Return Quantity",
        default=0,
        help="Manager-entered physical MAIN quantity accepted for this original delivered line.",
    )

    _sql_constraints = [
        (
            "bosta_return_case_original_line_unique",
            "unique(case_id, original_inventory_effect_line_id)",
            "Each original inventory line may appear only once in a return case.",
        ),
        (
            "bosta_return_case_line_qty_nonnegative",
            "CHECK(returned_quantity >= 0)",
            "Returned quantity cannot be negative.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return quantity audit lines are created only by the return engine."))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get(_RETURN_ENGINE_CONTEXT):
            return super().write(vals)
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager can approve returned quantities."))
        if set(vals) - {"returned_quantity"}:
            raise AccessError(_("Only the accepted returned quantity may be edited manually."))
        for line in self:
            if line.case_id.inspection_state in ("accepted", "rejected") or line.case_id.state in ("restored", "rejected"):
                raise ValidationError(_("Returned quantities are immutable after inspection is finalized."))
        return super().write(vals)

    @api.constrains("returned_quantity", "max_delivered_quantity")
    def _check_returned_quantity(self):
        for line in self:
            if line.returned_quantity < 0:
                raise ValidationError(_("Returned quantity must be non-negative."))
            if line.returned_quantity > line.max_delivered_quantity + 1e-9:
                raise ValidationError(_("Returned quantity cannot exceed the original delivered MAIN quantity."))

    def unlink(self):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return quantity audit lines cannot be deleted manually."))
        return super().unlink()


class BostaReturnRestorationEffect(models.Model):
    _name = "bosta.return.restoration.effect"
    _description = "Bosta Return Restoration Effect"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    return_case_id = fields.Many2one(
        "bosta.return.case", required=True, index=True, ondelete="restrict", check_company=True
    )
    return_delivery_id = fields.Many2one(
        "bosta.delivery", required=True, index=True, check_company=True, ondelete="restrict", readonly=True
    )
    original_delivery_id = fields.Many2one(
        "bosta.delivery", required=True, index=True, check_company=True, ondelete="restrict", readonly=True
    )
    return_type = fields.Selection(RETURN_TYPE_SELECTION, required=True, index=True, readonly=True)
    status = fields.Selection(
        RESTORATION_STATUS_SELECTION, required=True, default="pending", index=True
    )
    rule_code = fields.Char(readonly=True, index=True)
    picking_id = fields.Many2one(
        "stock.picking", check_company=True, ondelete="restrict", readonly=True
    )
    applied_at = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many(
        "bosta.return.restoration.effect.line", "effect_id", string="Restoration Lines", readonly=True
    )

    _sql_constraints = [
        (
            "bosta_return_restoration_case_unique",
            "unique(company_id, return_case_id)",
            "A return case can have only one restoration effect.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration effects are maintained only by the return engine."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration effects are maintained only by the return engine."))
        for effect in self:
            if effect.status != "applied":
                continue
            immutable = {
                "company_id", "return_case_id", "return_delivery_id", "original_delivery_id",
                "return_type", "picking_id", "applied_at", "rule_code", "status",
            }
            if immutable.intersection(vals):
                # Applied effects are historical audit rows; any attempted rewrite is blocked.
                raise ValidationError(_("Applied return restoration effects are immutable."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration effects cannot be deleted manually."))
        if any(effect.status == "applied" for effect in self):
            raise ValidationError(_("Applied return restoration effects cannot be deleted."))
        return super().unlink()


class BostaReturnRestorationEffectLine(models.Model):
    _name = "bosta.return.restoration.effect.line"
    _description = "Bosta Return Restoration Effect Line"
    _order = "id"
    _check_company_auto = True

    effect_id = fields.Many2one(
        "bosta.return.restoration.effect", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="effect_id.company_id", store=True, index=True)
    product_id = fields.Many2one(
        "product.product", required=True, check_company=True, ondelete="restrict", readonly=True
    )
    role = fields.Selection(RESTORATION_ROLE_SELECTION, required=True, readonly=True, index=True)
    quantity = fields.Float(required=True, readonly=True)
    source_location_id = fields.Many2one(
        "stock.location", required=True, check_company=True, ondelete="restrict", readonly=True
    )
    destination_location_id = fields.Many2one(
        "stock.location", required=True, check_company=True, ondelete="restrict", readonly=True
    )
    original_inventory_effect_line_id = fields.Many2one(
        "bosta.inventory.effect.line",
        required=True,
        check_company=True,
        ondelete="restrict",
        readonly=True,
        index=True,
    )

    _sql_constraints = [
        (
            "bosta_return_restoration_effect_line_role_unique",
            "unique(effect_id, original_inventory_effect_line_id, role)",
            "Each original inventory role can be restored only once per return effect.",
        ),
        (
            "bosta_return_restoration_positive_qty",
            "CHECK(quantity > 0)",
            "Restoration quantity must be positive.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration lines are maintained only by the return engine."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration lines are maintained only by the return engine."))
        if any(line.effect_id.status == "applied" for line in self):
            raise ValidationError(_("Applied restoration snapshots are immutable."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_RETURN_ENGINE_CONTEXT):
            raise AccessError(_("Return restoration lines cannot be deleted manually."))
        if any(line.effect_id.status == "applied" for line in self):
            raise ValidationError(_("Applied restoration snapshots are immutable."))
        return super().unlink()
