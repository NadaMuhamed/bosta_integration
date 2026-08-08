import hashlib
import logging
import os
import re
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.bosta_api_client import BostaApiClient
from ..services.bosta_extraction_service import BostaExtractionService
from ..services.bosta_inventory_service import BostaInventoryService
from ..services.bosta_product_mapping_service import BostaProductMappingService
from ..services.bosta_return_service import BostaReturnService
from ..services.bosta_persistence_service import BostaPersistenceService
from ..services.bosta_financial_service import BostaFinancialService
from ..services.bosta_financial_enrichment_service import BostaFinancialEnrichmentService
from ..services.exceptions import BostaApiError, BostaPersistenceError


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
_SYNC_STATUS_VALUES = [
    ("never", "Never"),
    ("running", "Running"),
    ("success", "Success"),
    ("partial", "Partial"),
    ("failed", "Failed"),
]
_AUTO_SYNC_STATUS_VALUES = [
    ("never", "Never"),
    ("success", "Success"),
    ("partial", "Partial"),
    ("failed", "Failed"),
    ("busy", "Busy / Skipped"),
]
_AUTO_SYNC_AUDIT_FIELDS = {
    "next_auto_sync_at",
    "last_auto_sync_at",
    "last_auto_sync_status",
    "last_auto_sync_error",
}

_SYNC_AUDIT_FIELDS = {
    "last_sync_started_at",
    "last_sync_completed_at",
    "last_sync_status",
    "last_sync_seen_count",
    "last_sync_created_count",
    "last_sync_updated_count",
    "last_sync_unchanged_count",
    "last_sync_conflict_count",
    "last_sync_error_count",
    "last_sync_error",
}
_PROTECTED_API_STATE_FIELDS = {
    "api_status",
    "last_api_test_at",
    "last_successful_api_request_at",
    "last_api_error",
} | _SYNC_AUDIT_FIELDS | _AUTO_SYNC_AUDIT_FIELDS


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

    last_sync_started_at = fields.Datetime(string="Last Sync Started", readonly=True, copy=False)
    last_sync_completed_at = fields.Datetime(string="Last Sync Completed", readonly=True, copy=False)
    last_sync_status = fields.Selection(
        selection=_SYNC_STATUS_VALUES,
        string="Last Sync Status",
        default="never",
        readonly=True,
        copy=False,
    )
    last_sync_seen_count = fields.Integer(string="Last Sync Seen", readonly=True, copy=False)
    last_sync_created_count = fields.Integer(string="Last Sync Created", readonly=True, copy=False)
    last_sync_updated_count = fields.Integer(string="Last Sync Updated", readonly=True, copy=False)
    last_sync_unchanged_count = fields.Integer(string="Last Sync Unchanged", readonly=True, copy=False)
    last_sync_conflict_count = fields.Integer(string="Last Sync Conflicts", readonly=True, copy=False)
    last_sync_error_count = fields.Integer(string="Last Sync Errors", readonly=True, copy=False)
    last_sync_error = fields.Text(string="Last Sync Error", readonly=True, copy=False)

    # Phase 9 scheduled synchronization is opt-in. One shared ir.cron selects
    # only configurations that are due; no network request is made on install.
    auto_sync_enabled = fields.Boolean(string="Auto Sync Enabled", default=False, copy=False)
    auto_sync_interval_minutes = fields.Integer(
        string="Auto Sync Interval (minutes)", default=15, required=True, copy=False
    )
    next_auto_sync_at = fields.Datetime(string="Next Auto Sync", readonly=True, copy=False, index=True)
    last_auto_sync_at = fields.Datetime(string="Last Auto Sync", readonly=True, copy=False)
    last_auto_sync_status = fields.Selection(
        _AUTO_SYNC_STATUS_VALUES, string="Last Auto Sync Status", default="never", readonly=True, copy=False
    )
    last_auto_sync_error = fields.Char(string="Last Auto Sync Error", readonly=True, copy=False)

    financial_details_enrichment_enabled = fields.Boolean(
        string="Financial Details Enrichment Enabled", default=False, copy=False
    )
    financial_details_batch_limit = fields.Integer(
        string="Financial Details Batch Limit", default=50, required=True, copy=False
    )

    # Phase 7 inventory is deliberately opt-in. Existing historical deliveries
    # cannot affect stock until a manager configures all required safeguards.
    inventory_sync_enabled = fields.Boolean(
        string="Inventory Sync Enabled",
        default=False,
        copy=False,
    )
    inventory_effective_from = fields.Datetime(
        string="Inventory Effective From",
        copy=False,
        help="Go-live cutoff. Historical Bosta deliveries before this point never deduct current Odoo stock.",
    )
    stock_source_location_id = fields.Many2one(
        "stock.location",
        string="Stock Source Location",
        domain="[('usage', '=', 'internal')]",
        check_company=True,
        ondelete="restrict",
    )
    bosta_transit_location_id = fields.Many2one(
        "stock.location",
        string="Bosta Transit Location",
        domain="[('usage', '=', 'transit')]",
        check_company=True,
        ondelete="restrict",
    )
    stock_picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Internal Operation Type",
        domain="[('code', '=', 'internal'), ('company_id', '=', company_id)]",
        check_company=True,
        ondelete="restrict",
        help="Optional when the company has exactly one internal operation type; required to disambiguate multiple warehouses.",
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
            raise AccessError(_("Bosta operational status fields can only be changed by integration controls."))

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
        records = super().create(prepared)
        now = fields.Datetime.now()
        for record in records.filtered("auto_sync_enabled"):
            record._write_auto_sync_state({"next_auto_sync_at": now})
        return records

    def write(self, vals):
        vals = dict(vals)
        self._ensure_no_external_api_state_fields(vals)
        enabling_auto = vals.get("auto_sync_enabled") is True
        disabling_auto = vals.get("auto_sync_enabled") is False
        if "api_base_url" in vals:
            vals["api_base_url"] = self._prepare_api_base_url(vals["api_base_url"])
        if "api_key_env_var" in vals:
            vals["api_key_env_var"] = self._prepare_api_key_env_var(vals["api_key_env_var"])
        result = super().write(vals)
        if enabling_auto:
            now = fields.Datetime.now()
            for record in self:
                if not record.next_auto_sync_at:
                    record._write_auto_sync_state({"next_auto_sync_at": now})
        elif disabling_auto:
            for record in self:
                record._write_auto_sync_state({"next_auto_sync_at": False})
        return result

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

    @api.constrains("auto_sync_interval_minutes", "auto_sync_enabled")
    def _check_auto_sync_interval(self):
        for record in self:
            if record.auto_sync_interval_minutes < 5:
                raise ValidationError(_("Auto sync interval must be at least 5 minutes."))

    @api.constrains("financial_details_batch_limit")
    def _check_financial_details_batch_limit(self):
        for record in self:
            if not 1 <= record.financial_details_batch_limit <= 200:
                raise ValidationError(_("Financial Details batch limit must be between 1 and 200."))

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

    @api.constrains(
        "inventory_sync_enabled",
        "inventory_effective_from",
        "stock_source_location_id",
        "bosta_transit_location_id",
        "stock_picking_type_id",
        "integration_enabled",
        "company_id",
    )
    def _check_inventory_configuration(self):
        for record in self:
            source = record.stock_source_location_id
            transit = record.bosta_transit_location_id
            picking_type = record.stock_picking_type_id
            for location in (source, transit):
                if location and location.company_id and location.company_id != record.company_id:
                    raise ValidationError(_("Bosta inventory locations must be company-compatible."))
            if source and source.usage != "internal":
                raise ValidationError(_("The Bosta stock source location must be Internal."))
            if transit and transit.usage != "transit":
                raise ValidationError(_("The Bosta transit location must use the Transit location type."))
            if source and transit and source == transit:
                raise ValidationError(_("The Bosta source and transit locations must be different."))
            if picking_type:
                if picking_type.code != "internal" or picking_type.company_id != record.company_id:
                    raise ValidationError(_("The Bosta internal operation type must be an Internal transfer for this company."))
            if not record.inventory_sync_enabled:
                continue
            if not record.integration_enabled:
                raise ValidationError(_("Enable the Bosta integration before enabling inventory sync."))
            if not record.inventory_effective_from:
                raise ValidationError(_("Set an inventory go-live cutoff before enabling Bosta inventory sync."))
            if not source or not transit:
                raise ValidationError(_("Configure both source and Bosta Transit locations before enabling inventory sync."))
            if not picking_type:
                internal_types = self.env["stock.picking.type"].sudo().search_count([
                    ("company_id", "=", record.company_id.id),
                    ("code", "=", "internal"),
                ])
                if internal_types != 1:
                    raise ValidationError(_("Select the Internal Operation Type explicitly when the company does not have exactly one internal transfer type."))

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

    def _write_sync_state(self, vals):
        self.ensure_one()
        safe_vals = {key: value for key, value in vals.items() if key in _SYNC_AUDIT_FIELDS}
        return super(BostaIntegrationConfig, self).write(safe_vals)

    def _write_auto_sync_state(self, vals):
        self.ensure_one()
        safe_vals = {key: value for key, value in vals.items() if key in _AUTO_SYNC_AUDIT_FIELDS}
        return super(BostaIntegrationConfig, self).write(safe_vals)

    def _next_auto_sync_value(self, now=None):
        self.ensure_one()
        from datetime import timedelta
        now = now or fields.Datetime.now()
        return now + timedelta(minutes=max(int(self.auto_sync_interval_minutes or 5), 5))

    def _sync_lock_key(self):
        self.ensure_one()
        material = f"bosta-sync:{self.env.cr.dbname}:{self.id}".encode("utf-8")
        # PostgreSQL's single-key advisory-lock API accepts signed bigint.  A
        # deterministic 63-bit digest avoids permanent flags and avoids
        # cross-database/config collisions without exposing company data.
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF

    def _try_acquire_sync_lock(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_try_advisory_lock(%s)",
            (self._sync_lock_key(),),
        )
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    def _release_sync_lock(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_unlock(%s)",
            (self._sync_lock_key(),),
        )

    @api.model
    def _sync_summary_state_values(self, summary, *, status, error=False, completed_at=None):
        return {
            "last_sync_completed_at": completed_at or fields.Datetime.now(),
            "last_sync_status": status,
            "last_sync_seen_count": summary.get("seen", 0),
            "last_sync_created_count": summary.get("created", 0),
            "last_sync_updated_count": summary.get("updated", 0),
            "last_sync_unchanged_count": summary.get("unchanged", 0),
            "last_sync_conflict_count": summary.get("conflicts", 0),
            "last_sync_error_count": summary.get("errors", 0),
            "last_sync_error": error or False,
        }

    @api.model
    def _sync_notification(self, summary, *, status, message=None):
        if message is None:
            message = _(
                "Bosta sync completed:\nSeen: %(seen)s\nCreated: %(created)s\n"
                "Updated: %(updated)s\nUnchanged: %(unchanged)s\n"
                "Conflicts: %(conflicts)s\nErrors: %(errors)s"
            ) % summary
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Delivery Sync"),
                "message": message,
                "type": "success" if status == "success" else ("warning" if status == "partial" else "danger"),
                "sticky": status != "success",
            },
        }

    def action_bootstrap_bosta_tester_links(self):
        self._ensure_manager_action_access()
        counts = BostaProductMappingService(self.env, self.company_id).bootstrap_tester_links()
        message = _(
            "Tester bootstrap completed:\nLinked: %(linked)s\nAlready linked: %(already_linked)s\n"
            "Conflicts: %(conflicts)s\nSkipped: %(skipped)s"
        ) % counts
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Tester Bootstrap"),
                "message": message,
                "type": "warning" if counts["conflicts"] else "success",
                "sticky": bool(counts["conflicts"]),
            },
        }

    def action_process_pending_inventory(self):
        self._ensure_manager_action_access()
        if not self.inventory_sync_enabled:
            raise UserError(_("Enable and fully configure Bosta inventory sync first."))
        if not self._try_acquire_sync_lock():
            raise UserError(_("A Bosta synchronization or inventory retry is already running for this configuration."))
        try:
            service = BostaInventoryService(self.env, self)
            cutoff = self.inventory_effective_from
            deliveries = self.env["bosta.delivery"].with_company(self.company_id).search([
                ("company_id", "=", self.company_id.id),
                ("flow_type", "=", "forward"),
                "|", "|",
                ("collected_from_business_at", ">=", cutoff),
                ("picked_up_at", ">=", cutoff),
                ("bosta_created_at", ">=", cutoff),
            ])
            counts = {"processed": 0, "applied": 0, "blocked": 0, "pending": 0}
            for delivery in deliveries:
                with self.env.cr.savepoint():
                    effect = service.process_delivery(delivery)
                counts["processed"] += 1
                if not effect:
                    continue
                if effect.status in ("outbound_applied", "delivered_finalized"):
                    counts["applied"] += 1
                elif effect.status.startswith("blocked") or effect.status == "exception":
                    counts["blocked"] += 1
                else:
                    counts["pending"] += 1
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Bosta Inventory Retry"),
                    "message": _(
                        "Processed: %(processed)s\nApplied/finalized: %(applied)s\nBlocked/review: %(blocked)s\nPending: %(pending)s"
                    ) % counts,
                    "type": "warning" if counts["blocked"] else "success",
                    "sticky": bool(counts["blocked"]),
                },
            }
        finally:
            self._release_sync_lock()

    def action_process_pending_returns(self):
        self._ensure_manager_action_access()
        if not self._try_acquire_sync_lock():
            raise UserError(_("A Bosta synchronization or return retry is already running for this configuration."))
        try:
            service = BostaReturnService(self.env, self)
            deliveries = self.env["bosta.delivery"].with_company(self.company_id).search([
                ("company_id", "=", self.company_id.id),
                ("flow_type", "in", ["return_to_origin", "customer_return"]),
            ])
            counts = {"processed": 0, "restored": 0, "review": 0, "pending": 0}
            for delivery in deliveries:
                with self.env.cr.savepoint():
                    case = service.process_delivery(delivery)
                if not case:
                    continue
                counts["processed"] += 1
                if case.state == "restored":
                    counts["restored"] += 1
                elif case.state in ("blocked", "review_required", "rejected"):
                    counts["review"] += 1
                else:
                    counts["pending"] += 1
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Bosta Return Retry"),
                    "message": _(
                        "Processed: %(processed)s\nRestored: %(restored)s\nReview/blocked: %(review)s\nPending: %(pending)s"
                    ) % counts,
                    "type": "warning" if counts["review"] else "success",
                    "sticky": bool(counts["review"]),
                },
            }
        finally:
            self._release_sync_lock()

    def action_sync_bosta_deliveries(self):
        self._ensure_manager_action_access()
        if not self.integration_enabled:
            raise UserError(_("Enable the Bosta integration before synchronization."))
        env_name = self.api_key_env_var or ""
        api_key = os.environ.get(env_name) if _ENV_VAR_RE.fullmatch(env_name) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise UserError(_("Configure the Bosta API key in the server environment before synchronization."))
        if not self._try_acquire_sync_lock():
            raise UserError(_("A Bosta synchronization is already running for this configuration."))

        try:
            summary = BostaPersistenceService.empty_summary()
            started_at = fields.Datetime.now()
            self._write_sync_state({
                "last_sync_started_at": started_at,
                "last_sync_completed_at": False,
                "last_sync_status": "running",
                "last_sync_seen_count": 0,
                "last_sync_created_count": 0,
                "last_sync_updated_count": 0,
                "last_sync_unchanged_count": 0,
                "last_sync_conflict_count": 0,
                "last_sync_error_count": 0,
                "last_sync_error": False,
            })
            client = self._build_api_client()
            extraction = BostaExtractionService(client)
            persistence = BostaPersistenceService(self.env)
            inventory = BostaInventoryService(self.env, self)
            returns = BostaReturnService(self.env, self)
            finance = BostaFinancialService(self.env, self)

            def _post_persist(delivery):
                # Preserve the accepted Phase 7/8 order: persistence/lifecycle,
                # inventory, returns, then Phase 9 financial evaluation.
                inventory.process_delivery(delivery)
                returns.process_delivery(delivery)
                finance.process_delivery(delivery)

            try:
                persistence.persist_search_deliveries(
                    extraction,
                    self.company_id,
                    page_size=self.page_size,
                    max_pages=self.max_pages,
                    summary=summary,
                    post_persist=_post_persist,
                )
                enrichment_result = False
                if (
                    self.env.context.get("bosta_run_financial_enrichment")
                    and self.financial_details_enrichment_enabled
                ):
                    enrichment_result = BostaFinancialEnrichmentService(
                        self.env, self, extraction, persistence
                    ).run()
            except BostaApiError as exc:
                api_status = getattr(exc, "status", "unknown_error")
                if api_status not in _API_STATUS_KEYS:
                    api_status = "unknown_error"
                safe_error = self._safe_status_message(api_status)
                self._write_api_state({
                    "api_status": api_status,
                    "last_api_error": safe_error,
                })
                self._write_sync_state(
                    self._sync_summary_state_values(
                        summary,
                        status="failed",
                        error=safe_error,
                    )
                )
                _logger.warning(
                    "Bosta delivery sync failed safely for company %s with status %s",
                    self.company_id.id,
                    getattr(exc, "status", "unknown_error"),
                )
                return self._sync_notification(
                    summary,
                    status="failed",
                    message=_("Bosta sync failed safely. Previously processed valid records were preserved."),
                )
            except BostaPersistenceError:
                safe_error = _("Bosta delivery persistence failed safely.")
                self._write_sync_state(
                    self._sync_summary_state_values(summary, status="failed", error=safe_error)
                )
                _logger.warning(
                    "Bosta delivery persistence failed safely for company %s",
                    self.company_id.id,
                )
                return self._sync_notification(summary, status="failed", message=safe_error)

            status = "partial" if summary["conflicts"] or summary["errors"] else "success"
            safe_error = _("Some Bosta deliveries could not be persisted safely.") if status == "partial" else False
            completed_api_at = fields.Datetime.now()
            enrichment_stop_status = (
                enrichment_result.get("stop_status") if enrichment_result else False
            )
            if enrichment_stop_status in _API_STATUS_KEYS:
                self._write_api_state({
                    "api_status": enrichment_stop_status,
                    "last_successful_api_request_at": completed_api_at,
                    "last_api_error": self._safe_status_message(enrichment_stop_status),
                })
            else:
                self._write_api_state({
                    "api_status": "connected",
                    "last_successful_api_request_at": completed_api_at,
                    "last_api_error": False,
                })
            self._write_sync_state(
                self._sync_summary_state_values(summary, status=status, error=safe_error)
            )
            _logger.info(
                "Bosta delivery sync completed for company %s: seen=%s created=%s updated=%s unchanged=%s conflicts=%s errors=%s",
                self.company_id.id,
                summary["seen"],
                summary["created"],
                summary["updated"],
                summary["unchanged"],
                summary["conflicts"],
                summary["errors"],
            )
            return self._sync_notification(summary, status=status)
        finally:
            self._release_sync_lock()

    def action_process_pending_financials(self):
        self._ensure_manager_action_access()
        counts = BostaFinancialService(self.env, self).process_company_pending(self.company_id)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bosta Financial Review"),
                "message": _(
                    "Processed: %(processed)s\nCalculated: %(calculated)s\nIncomplete: %(incomplete)s\nReview required: %(review_required)s"
                ) % counts,
                "type": "warning" if counts["incomplete"] or counts["review_required"] else "success",
                "sticky": bool(counts["incomplete"] or counts["review_required"]),
            },
        }

    @api.model
    def _cron_sync_due_configs(self):
        now = fields.Datetime.now()
        configs = self.sudo().search([
            ("active", "=", True),
            ("integration_enabled", "=", True),
            ("auto_sync_enabled", "=", True),
            ("next_auto_sync_at", "!=", False),
            ("next_auto_sync_at", "<=", now),
        ], order="next_auto_sync_at, id")
        for base_config in configs:
            config = base_config.sudo().with_company(base_config.company_id)
            env_name = config.api_key_env_var or ""
            api_key = os.environ.get(env_name) if _ENV_VAR_RE.fullmatch(env_name) else None
            next_at = config._next_auto_sync_value(now)
            if not isinstance(api_key, str) or not api_key.strip():
                config._write_auto_sync_state({
                    "last_auto_sync_at": now,
                    "last_auto_sync_status": "failed",
                    "last_auto_sync_error": "api_key_not_configured",
                    "next_auto_sync_at": next_at,
                })
                continue
            try:
                with self.env.cr.savepoint():
                    config.with_context(
                        bosta_run_financial_enrichment=True
                    ).action_sync_bosta_deliveries()
                auto_status = config.last_sync_status if config.last_sync_status in ("success", "partial") else "failed"
                safe_error = config.last_sync_error or False
            except UserError:
                # A same-config manual/cron overlap is safely blocked by the
                # accepted advisory lock. Do not expose exception text.
                auto_status = "busy"
                safe_error = "sync_busy_or_configuration_blocked"
            except Exception:
                # Per-config isolation: never log exception content/tracebacks here.
                # The underlying sync releases its advisory lock in its own finally
                # block; cron audit stores only this fixed non-sensitive code.
                _logger.error("Unexpected scheduled Bosta sync failure; sensitive details redacted")
                auto_status = "failed"
                safe_error = "unexpected_scheduled_sync_failure"
            config._write_auto_sync_state({
                "last_auto_sync_at": now,
                "last_auto_sync_status": auto_status,
                "last_auto_sync_error": safe_error,
                "next_auto_sync_at": next_at,
            })
        return True

