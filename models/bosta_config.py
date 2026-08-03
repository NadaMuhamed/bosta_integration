from odoo import fields, models


class BostaIntegrationConfig(models.Model):
    _name = "bosta.integration.config"
    _description = "Bosta Integration Configuration"
    _order = "company_id, id desc"

    name = fields.Char(
        string="Configuration Name",
        required=True,
        default="Bosta Configuration",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        (
            "bosta_config_company_unique",
            "unique(company_id)",
            "Only one Bosta integration configuration is allowed per company.",
        ),
    ]
