from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


_PRODUCT_GROUP = "bosta_integration.group_bosta_integration_manager"


class ProductProduct(models.Model):
    _inherit = "product.product"

    bosta_product_role = fields.Selection(
        [
            ("main", "Main / Bottle"),
            ("tester", "3 ML Tester"),
            ("other", "Other"),
        ],
        string="Bosta Product Role",
        default="other",
        index=True,
        groups=_PRODUCT_GROUP,
        help="Explicit persisted role used by Bosta inventory mapping. Runtime stock logic does not infer the role from the product name.",
    )
    bosta_tester_required = fields.Boolean(
        string="Bosta Tester Required",
        default=False,
        groups=_PRODUCT_GROUP,
        help="When enabled on a MAIN product, an explicit tester link is mandatory before inventory can move.",
    )
    bosta_tester_product_id = fields.Many2one(
        "product.product",
        string="Bosta 3 ML Tester",
        check_company=True,
        ondelete="restrict",
        groups=_PRODUCT_GROUP,
        help="Explicit 3 ML tester paired with this MAIN product.",
    )

    @api.constrains(
        "bosta_product_role",
        "bosta_tester_required",
        "bosta_tester_product_id",
        "company_id",
    )
    def _check_bosta_product_relation(self):
        for product in self:
            tester = product.bosta_tester_product_id
            if tester and tester == product:
                raise ValidationError(_("A product cannot be its own Bosta tester."))
            if product.bosta_product_role == "tester":
                if product.bosta_tester_required or tester:
                    raise ValidationError(_("A Bosta tester cannot require or link another tester."))
            if tester:
                if product.bosta_product_role != "main":
                    raise ValidationError(_("Only a Bosta MAIN product can have a tester link."))
                if tester.bosta_product_role != "tester":
                    raise ValidationError(_("The linked Bosta tester product must have role Tester."))
                if product.company_id and tester.company_id and product.company_id != tester.company_id:
                    raise ValidationError(_("Bosta MAIN and tester products must be company-compatible."))

            # Prevent changing an already-linked tester out of the tester role.
            if product.bosta_product_role != "tester":
                linked_main = self.search_count([
                    ("bosta_tester_product_id", "=", product.id),
                    ("id", "!=", product.id),
                ])
                if linked_main:
                    raise ValidationError(_("A product linked as a Bosta tester must keep the Tester role."))
