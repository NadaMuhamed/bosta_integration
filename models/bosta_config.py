from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import crypto_service


_DASHBOARD_HOST = "business.bosta.co"
_DASHBOARD_DEFAULT_URL = "https://business.bosta.co/orders"
_PROTECTED_SECRET_FIELDS = {
    "encrypted_dashboard_password",
    "credentials_updated_at",
    "credentials_updated_by",
}


class BostaIntegrationConfig(models.Model):
    _name = "bosta.integration.config"
    _description = "Bosta Integration Configuration"
    _order = "company_id, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Configuration Name",
        required=True,
        default="Bosta Integration",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        copy=False,
        ondelete="cascade",
    )
    integration_enabled = fields.Boolean(
        string="Integration Enabled",
        default=False,
        copy=False,
        help="Keep disabled until valid Dashboard credentials are configured.",
    )
    dashboard_url = fields.Char(
        string="Dashboard URL",
        required=True,
        default=_DASHBOARD_DEFAULT_URL,
        help="Secure Bosta Business Dashboard orders URL.",
    )
    dashboard_login = fields.Char(
        string="Dashboard Login",
        copy=False,
        help="Bosta Dashboard email address or phone number.",
    )
    dashboard_password_input = fields.Char(
        string="New Dashboard Password",
        store=False,
        copy=False,
        groups="bosta_integration.group_bosta_integration_manager",
        help="Enter a new password. This input is never stored as plaintext.",
    )
    encrypted_dashboard_password = fields.Char(
        string="Encrypted Dashboard Password",
        copy=False,
        groups="bosta_integration.group_bosta_integration_manager",
    )
    dashboard_password_configured = fields.Boolean(
        string="Password Configured",
        compute="_compute_dashboard_password_configured",
        store=True,
        readonly=True,
    )
    credentials_updated_at = fields.Datetime(
        string="Credentials Updated At",
        readonly=True,
        copy=False,
    )
    credentials_updated_by = fields.Many2one(
        comodel_name="res.users",
        string="Credentials Updated By",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    _sql_constraints = [
        (
            "bosta_config_company_unique",
            "unique(company_id)",
            "Only one Bosta integration configuration is allowed per company.",
        ),
    ]

    @api.depends("encrypted_dashboard_password")
    def _compute_dashboard_password_configured(self):
        for record in self:
            record.dashboard_password_configured = bool(
                record.encrypted_dashboard_password
            )

    @api.model
    def _normalize_dashboard_login(self, value):
        if not value:
            return False
        normalized = value.strip()
        if "@" in normalized:
            normalized = normalized.lower()
        return normalized or False

    @api.model
    def _prepare_dashboard_url(self, value):
        return value.strip() if isinstance(value, str) else value

    @api.model
    def _validate_dashboard_url_value(self, value):
        if not value:
            raise ValidationError(_("A Bosta Dashboard URL is required."))

        try:
            parsed = urlparse(value)
            port = parsed.port
        except (TypeError, ValueError):
            raise ValidationError(
                _("The Bosta Dashboard URL is malformed.")
            ) from None

        valid_path = parsed.path == "/orders" or parsed.path.startswith("/orders/")
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != _DASHBOARD_HOST
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or not valid_path
            or parsed.fragment
        ):
            raise ValidationError(
                _(
                    "The Dashboard URL must use HTTPS on business.bosta.co "
                    "and point to the /orders path."
                )
            )

    @api.model
    def _ensure_no_external_secret_fields(self, vals):
        attempted = _PROTECTED_SECRET_FIELDS.intersection(vals)
        if attempted:
            raise AccessError(
                _("Saved Dashboard credentials can only be changed through the secure controls.")
            )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for original_vals in vals_list:
            vals = dict(original_vals)
            self._ensure_no_external_secret_fields(vals)

            if "dashboard_url" in vals:
                vals["dashboard_url"] = self._prepare_dashboard_url(
                    vals["dashboard_url"]
                )
            if "dashboard_login" in vals:
                vals["dashboard_login"] = self._normalize_dashboard_login(
                    vals["dashboard_login"]
                )

            password = vals.pop("dashboard_password_input", False)
            credentials_changed = bool(vals.get("dashboard_login"))
            if password not in (False, None, ""):
                vals["encrypted_dashboard_password"] = (
                    crypto_service.encrypt_secret(password)
                )
                credentials_changed = True

            if credentials_changed:
                vals["credentials_updated_at"] = fields.Datetime.now()
                vals["credentials_updated_by"] = self.env.user.id

            prepared_vals_list.append(vals)

        return super().create(prepared_vals_list)

    def write(self, vals):
        vals = dict(vals)
        self._ensure_no_external_secret_fields(vals)

        if "dashboard_url" in vals:
            vals["dashboard_url"] = self._prepare_dashboard_url(
                vals["dashboard_url"]
            )

        credentials_changed = False
        if "dashboard_login" in vals:
            normalized_login = self._normalize_dashboard_login(
                vals["dashboard_login"]
            )
            credentials_changed = any(
                record.dashboard_login != normalized_login for record in self
            )
            vals["dashboard_login"] = normalized_login

        password = vals.pop("dashboard_password_input", False)
        if password not in (False, None, ""):
            vals["encrypted_dashboard_password"] = crypto_service.encrypt_secret(
                password
            )
            credentials_changed = True

        if credentials_changed:
            vals["credentials_updated_at"] = fields.Datetime.now()
            vals["credentials_updated_by"] = self.env.user.id

        return super().write(vals)

    @api.constrains("dashboard_url")
    def _check_dashboard_url(self):
        for record in self:
            self._validate_dashboard_url_value(record.dashboard_url)

    @api.constrains(
        "integration_enabled",
        "dashboard_url",
        "dashboard_login",
        "encrypted_dashboard_password",
    )
    def _check_enabled_configuration(self):
        for record in self:
            if not record.integration_enabled:
                continue
            if not (
                record.dashboard_url
                and record.dashboard_login
                and record.encrypted_dashboard_password
            ):
                raise ValidationError(
                    _(
                        "Configure the Dashboard URL, login, and saved password "
                        "before enabling the Bosta integration."
                    )
                )

    def action_clear_saved_password(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(
                _("Only a Bosta Integration Manager can clear the saved password.")
            )

        return super(BostaIntegrationConfig, self).write(
            {
                "encrypted_dashboard_password": False,
                "credentials_updated_at": fields.Datetime.now(),
                "credentials_updated_by": self.env.user.id,
                "integration_enabled": False,
            }
        )
