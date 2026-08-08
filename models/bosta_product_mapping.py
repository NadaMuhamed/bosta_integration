from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..services.bosta_product_helpers import canonical_business_code


class BostaProductMapping(models.Model):
    _name = "bosta.product.mapping"
    _description = "Bosta Product Mapping"
    _order = "mapping_status, source_title, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        index=True, ondelete="cascade",
    )
    active = fields.Boolean(default=True)
    creation_source = fields.Char(required=True, default="BOSTA", index=True)
    external_product_id = fields.Char(string="Bosta Product ID", index=True)
    bosta_product_info_id = fields.Char(string="Bosta ProductInfo ID", index=True)
    source_product_code = fields.Char(string="Source Business Code", index=True)
    source_title = fields.Char(string="Bosta Product Title")
    identity_key = fields.Char(compute="_compute_identity_key", store=True, index=True)
    mapping_status = fields.Selection(
        [("mapped", "Mapped"), ("unmatched", "Unmatched"), ("conflict", "Conflict")],
        required=True, default="unmatched", index=True,
    )
    odoo_product_id = fields.Many2one(
        "product.product", string="Odoo MAIN Product", check_company=True,
        ondelete="restrict", index=True,
    )
    mapping_method = fields.Selection(
        [
            ("manual", "Manual"),
            ("external_id", "Existing External ID"),
            ("exact_business_code", "Exact Business Code"),
            ("bootstrap", "Bootstrap"),
        ],
        index=True,
    )
    last_seen_at = fields.Datetime(readonly=True)
    seen_count = fields.Integer(default=0, readonly=True)

    _sql_constraints = [
        (
            "bosta_mapping_identity_unique",
            "unique(company_id, creation_source, identity_key)",
            "A Bosta product identity can have only one mapping per company and source.",
        ),
    ]

    @api.depends("external_product_id", "source_product_code")
    def _compute_identity_key(self):
        for record in self:
            if record.external_product_id:
                record.identity_key = "external:%s" % record.external_product_id.strip()
            elif record.source_product_code:
                record.identity_key = "code:%s" % record.source_product_code.strip()
            else:
                record.identity_key = False

    @api.model
    def _clean_mapping_vals(self, vals):
        vals = dict(vals)
        for field_name in (
            "creation_source", "external_product_id", "bosta_product_info_id",
            "source_title",
        ):
            if field_name in vals and isinstance(vals[field_name], str):
                vals[field_name] = vals[field_name].strip() or False
        if "source_product_code" in vals:
            vals["source_product_code"] = canonical_business_code(vals["source_product_code"]) or False
        return vals

    @staticmethod
    def _has_stable_identity(external_product_id, source_product_code):
        return bool(external_product_id or source_product_code)

    @api.model
    def _validate_stable_identity_vals(self, vals, record=False):
        external_product_id = vals.get(
            "external_product_id", record.external_product_id if record else False
        )
        source_product_code = vals.get(
            "source_product_code", record.source_product_code if record else False
        )
        if not self._has_stable_identity(external_product_id, source_product_code):
            raise ValidationError(_(
                "A Bosta product mapping requires a stable external product ID or business code."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        cleaned_vals_list = [self._clean_mapping_vals(vals) for vals in vals_list]
        for vals in cleaned_vals_list:
            self._validate_stable_identity_vals(vals)
        return super().create(cleaned_vals_list)

    def write(self, vals):
        vals = self._clean_mapping_vals(vals)
        for record in self:
            self._validate_stable_identity_vals(vals, record=record)
        if "odoo_product_id" in vals and vals.get("odoo_product_id") and "mapping_status" not in vals:
            vals["mapping_status"] = "mapped"
            vals.setdefault("mapping_method", "manual")
        return super().write(vals)

    @api.constrains("external_product_id", "source_product_code")
    def _check_mapping_identity(self):
        for record in self:
            if not self._has_stable_identity(record.external_product_id, record.source_product_code):
                raise ValidationError(_(
                    "A Bosta product mapping requires a stable external product ID or business code."
                ))

    @api.constrains("mapping_status", "odoo_product_id", "company_id")
    def _check_mapped_product(self):
        for record in self:
            product = record.odoo_product_id
            if record.mapping_status == "mapped" and not product:
                raise ValidationError(_("A mapped Bosta product requires an Odoo MAIN product."))
            if product:
                if product.bosta_product_role != "main":
                    raise ValidationError(_("A Bosta mapping must target an explicit MAIN product."))
                if record.company_id and product.company_id and record.company_id != product.company_id:
                    raise ValidationError(_("The mapped product is not compatible with this company."))
