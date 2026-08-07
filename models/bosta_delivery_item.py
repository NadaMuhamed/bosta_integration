from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BostaDeliveryItem(models.Model):
    _name = "bosta.delivery.item"
    _description = "Bosta Delivery Item"
    _order = "delivery_id, sequence, id"
    _check_company_auto = True

    delivery_id = fields.Many2one(
        "bosta.delivery",
        string="Delivery",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="delivery_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    bosta_product_info_id = fields.Char(string="Bosta Product Info ID", index=True)
    external_product_id = fields.Char(
        string="External Product ID",
        index=True,
        help="Normalized Bosta productInfo.productId. Product mapping is deferred to a later phase.",
    )
    title = fields.Char(string="Title")
    quantity = fields.Float(string="Quantity", default=1.0)
    product_type = fields.Char(string="Product Type")
    options_string = fields.Text(string="Options")

    @api.constrains("quantity")
    def _check_quantity(self):
        for record in self:
            if record.quantity < 0:
                raise ValidationError(_("Bosta delivery item quantity cannot be negative."))
