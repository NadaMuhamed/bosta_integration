import logging
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import crypto_service
from ..services.dashboard_auth_service import DashboardAuthService


_logger = logging.getLogger(__name__)
_DASHBOARD_HOST = "business.bosta.co"
_DASHBOARD_DEFAULT_URL = "https://business.bosta.co/orders"
_SESSION_STATUS_VALUES = [
    ("not_configured", "Not Configured"),
    ("authenticated", "Authenticated"),
    ("expired", "Expired"),
    ("invalid_credentials", "Invalid Credentials"),
    ("otp_required", "OTP Required"),
    ("captcha_required", "CAPTCHA Required"),
    ("blocked", "Blocked"),
    ("connection_failed", "Connection Failed"),
    ("browser_unavailable", "Browser Unavailable"),
    ("contract_changed", "Dashboard Contract Changed"),
    ("unknown_error", "Unknown Error"),
]
_SESSION_STATUS_KEYS = {value for value, _label in _SESSION_STATUS_VALUES}
_SAFE_STATUS_MESSAGES = {
    "not_configured": "No Bosta Dashboard session is configured.",
    "authenticated": "Bosta Dashboard authentication succeeded.",
    "expired": "The saved Bosta Dashboard session is invalid or expired.",
    "invalid_credentials": "The Bosta Dashboard login or password was rejected.",
    "otp_required": "Bosta Dashboard requires a one-time verification code.",
    "captcha_required": "Bosta Dashboard requires CAPTCHA verification.",
    "blocked": "Bosta Dashboard has blocked this authentication attempt.",
    "connection_failed": "The Odoo server could not reach the Bosta Dashboard safely.",
    "browser_unavailable": "Chromium is unavailable in the Odoo server environment.",
    "contract_changed": "The Bosta Dashboard login page structure is no longer recognized.",
    "unknown_error": "Bosta Dashboard authentication failed safely.",
}
_PROTECTED_INTERNAL_FIELDS = {
    "encrypted_dashboard_password",
    "credentials_updated_at",
    "credentials_updated_by",
    "encrypted_session_state",
    "session_status",
    "last_login_attempt_at",
    "last_successful_login_at",
    "last_session_validation_at",
    "last_auth_error",
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
    encrypted_session_state = fields.Text(
        string="Encrypted Dashboard Session",
        readonly=True,
        copy=False,
        groups="bosta_integration.group_bosta_integration_manager",
    )
    session_configured = fields.Boolean(
        string="Session Configured",
        compute="_compute_session_configured",
        store=True,
        readonly=True,
    )
    session_status = fields.Selection(
        selection=_SESSION_STATUS_VALUES,
        string="Session Status",
        default="not_configured",
        readonly=True,
        copy=False,
    )
    last_login_attempt_at = fields.Datetime(
        string="Last Login Attempt",
        readonly=True,
        copy=False,
    )
    last_successful_login_at = fields.Datetime(
        string="Last Successful Login",
        readonly=True,
        copy=False,
    )
    last_session_validation_at = fields.Datetime(
        string="Last Session Validation",
        readonly=True,
        copy=False,
    )
    last_auth_error = fields.Text(
        string="Last Authentication Error",
        readonly=True,
        copy=False,
    )
    browser_timeout_seconds = fields.Integer(
        string="Browser Timeout (seconds)",
        default=30,
        required=True,
        help="Timeout used for Dashboard navigation and locator operations.",
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

    @api.depends("encrypted_session_state")
    def _compute_session_configured(self):
        for record in self:
            record.session_configured = bool(record.encrypted_session_state)

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
    def _ensure_no_external_internal_fields(self, vals):
        attempted = _PROTECTED_INTERNAL_FIELDS.intersection(vals)
        if attempted:
            raise AccessError(
                _(
                    "Saved Dashboard credentials and authentication state can "
                    "only be changed through secure controls."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for original_vals in vals_list:
            vals = dict(original_vals)
            self._ensure_no_external_internal_fields(vals)

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
        self._ensure_no_external_internal_fields(vals)

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

    @api.constrains("browser_timeout_seconds")
    def _check_browser_timeout_seconds(self):
        for record in self:
            if not 5 <= record.browser_timeout_seconds <= 120:
                raise ValidationError(
                    _("Browser timeout must be between 5 and 120 seconds.")
                )

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

    def _ensure_manager_action_access(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(
                _("Only a Bosta Integration Manager can perform this action.")
            )
        self.check_access("write")

    @api.model
    def _safe_status_message(self, status):
        return _SAFE_STATUS_MESSAGES.get(
            status, _SAFE_STATUS_MESSAGES["unknown_error"]
        )

    def _apply_auth_result(self, result):
        self.ensure_one()
        if result.status not in _SESSION_STATUS_KEYS:
            raise ValidationError(
                _("The authentication service returned an invalid status.")
            )

        now = fields.Datetime.now()
        vals = {
            "session_status": result.status,
            "last_auth_error": False
            if result.success
            else self._safe_status_message(result.status),
        }
        if result.attempted_fresh_login:
            vals["last_login_attempt_at"] = now
        if result.success:
            vals["last_session_validation_at"] = now
            if result.attempted_fresh_login:
                vals["last_successful_login_at"] = now
        if result.encrypted_session_state is not None:
            vals["encrypted_session_state"] = result.encrypted_session_state
        elif result.clear_session:
            vals["encrypted_session_state"] = False

        return super(BostaIntegrationConfig, self).write(vals)

    def action_test_dashboard_login(self):
        self._ensure_manager_action_access()
        result = DashboardAuthService(self).authenticate()
        self._apply_auth_result(result)
        safe_message = (
            result.message
            if result.success
            else self._safe_status_message(result.status)
        )

        if not result.success:
            _logger.warning(
                "Bosta Dashboard authentication completed with safe status: %s",
                result.status,
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Dashboard Authentication"),
                "message": safe_message,
                "type": "success" if result.success else "danger",
                "sticky": not result.success,
            },
        }

    def action_reset_dashboard_session(self):
        self._ensure_manager_action_access()
        super(BostaIntegrationConfig, self).write(
            {
                "encrypted_session_state": False,
                "session_status": "not_configured",
                "last_auth_error": False,
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Dashboard Session"),
                "message": _("The saved Bosta Dashboard session was reset."),
                "type": "success",
                "sticky": False,
            },
        }

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
