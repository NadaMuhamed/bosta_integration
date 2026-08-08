from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


_ENGINE_CONTEXT = "bosta_inventory_engine"


class BostaInventoryEffect(models.Model):
    _name = "bosta.inventory.effect"
    _description = "Bosta Inventory Effect"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    delivery_id = fields.Many2one("bosta.delivery", required=True, index=True, ondelete="cascade", check_company=True)
    status = fields.Selection(
        [
            ("not_applicable", "Not Applicable"),
            ("pending_departure", "Pending Departure"),
            ("blocked_mapping", "Blocked: Mapping"),
            ("blocked_tester", "Blocked: Tester"),
            ("blocked_stock", "Blocked: Stock"),
            ("ready", "Ready"),
            ("outbound_applied", "Outbound Applied"),
            ("delivered_finalized", "Delivered Finalized"),
            ("exception", "Exception / Review"),
        ],
        required=True, default="pending_departure", index=True,
    )
    source_location_id = fields.Many2one("stock.location", check_company=True, ondelete="restrict")
    transit_location_id = fields.Many2one("stock.location", check_company=True, ondelete="restrict")
    outbound_picking_id = fields.Many2one("stock.picking", check_company=True, ondelete="restrict", readonly=True)
    final_picking_id = fields.Many2one("stock.picking", check_company=True, ondelete="restrict", readonly=True)
    outbound_applied_at = fields.Datetime(readonly=True)
    delivered_finalized_at = fields.Datetime(readonly=True)
    blocked_reason = fields.Char(readonly=True)
    line_ids = fields.One2many("bosta.inventory.effect.line", "effect_id", string="Inventory Lines", readonly=True)

    _sql_constraints = [
        (
            "bosta_inventory_delivery_unique",
            "unique(company_id, delivery_id)",
            "A Bosta delivery can have only one inventory effect per company.",
        ),
    ]

    @api.model
    def _engine_allowed(self):
        return bool(self.env.context.get(_ENGINE_CONTEXT))

    @api.model_create_multi
    def create(self, vals_list):
        if not self._engine_allowed():
            raise AccessError(_("Bosta inventory effects are maintained only by the inventory engine."))
        return super().create(vals_list)

    def write(self, vals):
        if not self._engine_allowed():
            raise AccessError(_("Bosta inventory effects are maintained only by the inventory engine."))
        for record in self:
            if not record.outbound_picking_id:
                continue
            for field_name in ("source_location_id", "transit_location_id"):
                if field_name not in vals:
                    continue
                new_id = vals.get(field_name) or False
                current_id = record[field_name].id or False
                if new_id != current_id:
                    raise ValidationError(_(
                        "Bosta inventory locations are immutable after the outbound picking has been applied."
                    ))
        return super().write(vals)

    @api.constrains("outbound_picking_id", "source_location_id", "transit_location_id")
    def _check_outbound_location_snapshot(self):
        for record in self:
            picking = record.outbound_picking_id
            if not picking:
                continue
            if (
                picking.location_id != record.source_location_id
                or picking.location_dest_id != record.transit_location_id
            ):
                raise ValidationError(_(
                    "Bosta inventory audit locations must match the applied outbound picking."
                ))

    def unlink(self):
        if not self._engine_allowed():
            raise AccessError(_("Bosta inventory effects cannot be deleted manually."))
        return super().unlink()


class BostaInventoryEffectLine(models.Model):
    _name = "bosta.inventory.effect.line"
    _description = "Bosta Inventory Effect Line"
    _order = "id"
    _check_company_auto = True

    effect_id = fields.Many2one("bosta.inventory.effect", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="effect_id.company_id", store=True, index=True)
    mapping_id = fields.Many2one("bosta.product.mapping", check_company=True, ondelete="set null")
    source_external_product_id = fields.Char(readonly=True)
    source_product_code = fields.Char(readonly=True)
    source_title = fields.Char(readonly=True)
    main_product_id = fields.Many2one("product.product", required=True, check_company=True, ondelete="restrict", readonly=True)
    tester_product_id = fields.Many2one("product.product", check_company=True, ondelete="restrict", readonly=True)
    main_quantity = fields.Float(required=True, readonly=True)
    tester_quantity = fields.Float(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_ENGINE_CONTEXT):
            raise AccessError(_("Bosta inventory effect lines are maintained only by the inventory engine."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get(_ENGINE_CONTEXT):
            raise AccessError(_("Bosta inventory effect lines are maintained only by the inventory engine."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_ENGINE_CONTEXT):
            raise AccessError(_("Bosta inventory effect lines are maintained only by the inventory engine."))
        return super().unlink()
