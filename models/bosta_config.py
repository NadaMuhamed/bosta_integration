import logging
import os
import re
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services.bosta_api_client import BostaApiClient
from ..services.exceptions import BostaApiError


_logger = logging.getLogger(__name__)
_API_DEFAULT_BASE_URL = "https://app.bosta.co"
_API_HOST = "app.bosta.co"
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_API_STATUS_VALUES = [
    ("not_configured", "Not Configured"),
    ("connected", "Connected"),
    ("authentication_failed", "Authentication Failed"),
    ("permission_denied", "Permission Denied"),
    ("rate_limited", "Rate Limited"),
    ("connection_failed", "Connection Failed"),
    ("timeout", "Timeout"),
    ("contract_error", "Contract Error"),
    ("server_error", "Server Error"),
    ("unknown_error", "Unknown Error"),
]
_API_STATUS_KEYS = {value for value, _label in _API_STATUS_VALUES}
_SAFE_STATUS_MESSAGES = {
    "not_configured": "The Bosta API key is not configured in the server environment.",
    "connected": "The Bosta API connection succeeded.",
    "authentication_failed": "Bosta API authentication failed.",
    "permission_denied": "The Bosta API key does not have permission for this operation.",
    "rate_limited": "The Bosta API rate limit was reached.",
    "connection_failed": "The Odoo server could not reach the Bosta API.",
    "timeout": "The Bosta API request timed out.",
    "contract_error": "The Bosta API returned an unexpected response format.",
    "server_error": "The Bosta API is temporarily unavailable.",
    "unknown_error": "The Bosta API request failed safely.",
}
_PROTECTED_API_STATE_FIELDS = {
    "api_status",
    "last_api_test_at",
    "last_successful_api_request_at",
    "last_api_error",
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
        help="Enable only after the Bosta API configuration is valid and the environment key exists.",
    )

    api_base_url = fields.Char(
        string="API Base URL",
        required=True,
        default=_API_DEFAULT_BASE_URL,
        help="Only the official Bosta API origin is accepted.",
    )
    api_key_env_var = fields.Char(
        string="API Key Environment Variable",
        required=True,
        default="BOSTA_API_KEY",
        copy=False,
        help="Name of the server environment variable containing the Bosta API key. The key itself is never stored in Odoo.",
    )
    api_key_configured = fields.Boolean(
        string="API Key Configured",
        compute="_compute_api_key_configured",
        store=False,
        readonly=True,
        help="Indicates only whether the configured environment variable currently contains a non-empty value.",
    )
    request_timeout_seconds = fields.Integer(
        string="Request Timeout (seconds)",
        required=True,
        default=30,
    )
    page_size = fields.Integer(
        string="Page Size",
        required=True,
        default=1500,
        help="Maximum deliveries requested per Bosta search page. Bosta requests are capped at 1500.",
    )
    max_pages = fields.Integer(
        string="Max Pages",
        required=True,
        default=10000,
        help="Safety limit that prevents infinite pagination loops.",
    )

    api_status = fields.Selection(
        selection=_API_STATUS_VALUES,
        string="API Status",
        default="not_configured",
        readonly=True,
        copy=False,
    )
    last_api_test_at = fields.Datetime(
        string="Last API Test",
        readonly=True,
        copy=False,
    )
    last_successful_api_request_at = fields.Datetime(
        string="Last Successful API Request",
        readonly=True,
        copy=False,
    )
    last_api_error = fields.Text(
        string="Last API Error",
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            "bosta_config_company_unique",
            "unique(company_id)",
            "Only one Bosta integration configuration is allowed per company.",
        ),
    ]

    @api.depends("api_key_env_var")
    def _compute_api_key_configured(self):
        for record in self:
            env_name = record.api_key_env_var or ""
            value = os.environ.get(env_name) if _ENV_VAR_RE.fullmatch(env_name) else None
            record.api_key_configured = bool(isinstance(value, str) and value.strip())

    @api.model
    def _prepare_api_base_url(self, value):
        if not isinstance(value, str):
            return value
        return value.strip().rstrip("/")

    @api.model
    def _prepare_api_key_env_var(self, value):
        if not isinstance(value, str):
            return value
        return value.strip()

    @api.model
    def _validate_api_base_url_value(self, value):
        if not value:
            raise ValidationError(_("A Bosta API base URL is required."))
        try:
            parsed = urlparse(value)
            port = parsed.port
        except (TypeError, ValueError):
            raise ValidationError(_("The Bosta API base URL is malformed.")) from None
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != _API_HOST
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError(
                _("The API base URL must be exactly the HTTPS origin https://app.bosta.co.")
            )

    @api.model
    def _validate_api_key_env_var_value(self, value):
        if not isinstance(value, str) or not _ENV_VAR_RE.fullmatch(value):
            raise ValidationError(
                _("The API key environment-variable name must use only uppercase letters, digits, and underscores and must not start with a digit.")
            )

    @api.model
    def _ensure_no_external_api_state_fields(self, vals):
        if _PROTECTED_API_STATE_FIELDS.intersection(vals):
            raise AccessError(_("Bosta API status fields can only be changed by integration controls."))

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for original_vals in vals_list:
            vals = dict(original_vals)
            self._ensure_no_external_api_state_fields(vals)
            if "api_base_url" in vals:
                vals["api_base_url"] = self._prepare_api_base_url(vals["api_base_url"])
            if "api_key_env_var" in vals:
                vals["api_key_env_var"] = self._prepare_api_key_env_var(vals["api_key_env_var"])
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        vals = dict(vals)
        self._ensure_no_external_api_state_fields(vals)
        if "api_base_url" in vals:
            vals["api_base_url"] = self._prepare_api_base_url(vals["api_base_url"])
        if "api_key_env_var" in vals:
            vals["api_key_env_var"] = self._prepare_api_key_env_var(vals["api_key_env_var"])
        return super().write(vals)

    @api.constrains("api_base_url")
    def _check_api_base_url(self):
        for record in self:
            self._validate_api_base_url_value(record.api_base_url)

    @api.constrains("api_key_env_var")
    def _check_api_key_env_var(self):
        for record in self:
            self._validate_api_key_env_var_value(record.api_key_env_var)

    @api.constrains("request_timeout_seconds")
    def _check_request_timeout_seconds(self):
        for record in self:
            if not 5 <= record.request_timeout_seconds <= 120:
                raise ValidationError(_("Request timeout must be between 5 and 120 seconds."))

    @api.constrains("page_size")
    def _check_page_size(self):
        for record in self:
            if not 1 <= record.page_size <= 1500:
                raise ValidationError(_("Page size must be between 1 and 1500."))

    @api.constrains("max_pages")
    def _check_max_pages(self):
        for record in self:
            if not 1 <= record.max_pages <= 10000:
                raise ValidationError(_("Maximum pages must be between 1 and 10000."))

    @api.constrains(
        "integration_enabled",
        "api_base_url",
        "api_key_env_var",
        "request_timeout_seconds",
        "page_size",
        "max_pages",
    )
    def _check_enabled_configuration(self):
        for record in self:
            if not record.integration_enabled:
                continue
            self._validate_api_base_url_value(record.api_base_url)
            self._validate_api_key_env_var_value(record.api_key_env_var)
            value = os.environ.get(record.api_key_env_var)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    _("Configure the Bosta API key in the server environment before enabling the integration.")
                )

    def _ensure_manager_action_access(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
            "bosta_integration.group_bosta_integration_manager"
        ):
            raise AccessError(_("Only a Bosta Integration Manager can perform this action."))
        self.check_access("write")

    def _build_api_client(self, **overrides):
        self.ensure_one()
        values = {
            "base_url": self.api_base_url,
            "api_key_env_var": self.api_key_env_var,
            "timeout": self.request_timeout_seconds,
            "page_size": self.page_size,
            "max_pages": self.max_pages,
        }
        values.update(overrides)
        return BostaApiClient(**values)

    @api.model
    def _safe_status_message(self, status):
        return _SAFE_STATUS_MESSAGES.get(status, _SAFE_STATUS_MESSAGES["unknown_error"])

    def _write_api_state(self, vals):
        self.ensure_one()
        return super(BostaIntegrationConfig, self).write(vals)

    def action_test_api_connection(self):
        self._ensure_manager_action_access()
        now = fields.Datetime.now()
        try:
            self._build_api_client().test_connection()
        except BostaApiError as exc:
            status = exc.status if exc.status in _API_STATUS_KEYS else "unknown_error"
            safe_message = self._safe_status_message(status)
            self._write_api_state(
                {
                    "api_status": status,
                    "last_api_test_at": now,
                    "last_api_error": safe_message,
                }
            )
            _logger.warning("Bosta API connection test completed with safe status: %s", status)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Bosta API Connection"),
                    "message": safe_message,
                    "type": "danger",
                    "sticky": True,
                },
            }
        except Exception:
            status = "unknown_error"
            safe_message = self._safe_status_message(status)
            self._write_api_state(
                {
                    "api_status": status,
                    "last_api_test_at": now,
                    "last_api_error": safe_message,
                }
            )
            _logger.error("Unexpected Bosta API connection test failure; details redacted")
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Bosta API Connection"),
                    "message": safe_message,
                    "type": "danger",
                    "sticky": True,
                },
            }

        self._write_api_state(
            {
                "api_status": "connected",
                "last_api_test_at": now,
                "last_successful_api_request_at": now,
                "last_api_error": False,
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta API Connection"),
                "message": _("The Bosta API connection succeeded."),
                "type": "success",
                "sticky": False,
            },
        }
