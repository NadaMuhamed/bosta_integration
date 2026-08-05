from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase, tagged

from ..services import crypto_service
from ..services.auth_result import AuthResult


@tagged("post_install", "-at_install")
class TestAuthenticationActions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Phase 2 A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Phase 2 B"})
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager_group = cls.env.ref(
            "bosta_integration.group_bosta_integration_manager"
        )
        cls.manager_a = cls._create_test_user(
            "phase2_manager_a", cls.company_a, [cls.company_a], cls.manager_group
        )
        cls.integration_user = cls._create_test_user(
            "phase2_integration_user",
            cls.company_a,
            [cls.company_a],
            cls.user_group,
        )
        cls.key = Fernet.generate_key().decode("ascii")
        cls.password = "phase2-action-private-password"

    @classmethod
    def _create_test_user(cls, login, company, companies, group):
        return cls.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": login.replace("_", " ").title(),
                "login": login,
                "email": f"{login}@example.invalid",
                "company_id": company.id,
                "company_ids": [(6, 0, [item.id for item in companies])],
                "groups_id": [(6, 0, [group.id])],
            }
        )

    def _key_environment(self):
        return patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: self.key},
            clear=True,
        )

    def _manager_model(self):
        return self.Config.with_user(self.manager_a).with_context(
            allowed_company_ids=[self.company_a.id]
        )

    def _create_manager_config(self, **values):
        vals = {
            "company_id": self.company_a.id,
            "dashboard_login": "manager@example.com",
            "dashboard_password_input": self.password,
        }
        vals.update(values)
        with self._key_environment():
            return self._manager_model().create(vals)

    def test_phase2_fields_have_correct_defaults(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        self.assertFalse(config.encrypted_session_state)
        self.assertFalse(config.session_configured)
        self.assertEqual(config.session_status, "not_configured")
        self.assertEqual(config.browser_timeout_seconds, 30)
        self.assertFalse(config.last_login_attempt_at)
        self.assertFalse(config.last_successful_login_at)
        self.assertFalse(config.last_session_validation_at)
        self.assertFalse(config.last_auth_error)

    def test_browser_timeout_accepts_valid_values(self):
        config = self.Config.sudo().create(
            {"company_id": self.company_a.id, "browser_timeout_seconds": 5}
        )
        config.write({"browser_timeout_seconds": 120})
        self.assertEqual(config.browser_timeout_seconds, 120)

    def test_browser_timeout_rejects_value_below_five(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Config.sudo().create(
                {"company_id": self.company_a.id, "browser_timeout_seconds": 4}
            )

    def test_browser_timeout_rejects_value_above_one_hundred_twenty(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.Config.sudo().create(
                {"company_id": self.company_a.id, "browser_timeout_seconds": 121}
            )

    def test_direct_write_to_encrypted_session_state_is_rejected(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        with self.assertRaises(AccessError):
            config.write({"encrypted_session_state": "forbidden"})

    def test_direct_write_to_session_audit_and_status_fields_is_rejected(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        protected_values = (
            {"session_status": "authenticated"},
            {"last_login_attempt_at": fields.Datetime.now()},
            {"last_successful_login_at": fields.Datetime.now()},
            {"last_session_validation_at": fields.Datetime.now()},
            {"last_auth_error": "forbidden"},
        )
        for vals in protected_values:
            with self.assertRaises(AccessError):
                config.write(vals)

    def test_caller_context_cannot_bypass_session_field_protection(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        with self.assertRaises(AccessError):
            config.with_context(_bosta_internal_session_write=True).write(
                {"session_status": "authenticated"}
            )

    def test_direct_create_with_internal_session_fields_is_rejected(self):
        with self.assertRaises(AccessError):
            self.Config.sudo().create(
                {
                    "company_id": self.company_a.id,
                    "encrypted_session_state": "forbidden",
                }
            )

    def test_ordinary_integration_user_cannot_press_test_login(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        user_record = config.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        with self.assertRaises(AccessError):
            user_record.action_test_dashboard_login()

    def test_manager_can_press_test_login_for_allowed_company(self):
        config = self._create_manager_config()
        result = AuthResult(
            success=True,
            status="authenticated",
            message="Bosta Dashboard authentication succeeded.",
            encrypted_session_state="encrypted-session-ciphertext",
            attempted_fresh_login=True,
        )
        with patch(
            "odoo.addons.bosta_integration.models.bosta_config.DashboardAuthService"
        ) as service_class:
            service_class.return_value.authenticate.return_value = result
            notification = config.action_test_dashboard_login()
        self.assertEqual(notification["params"]["type"], "success")
        self.assertEqual(config.session_status, "authenticated")
        self.assertTrue(config.session_configured)

    def test_manager_cannot_press_test_login_for_disallowed_company(self):
        config_b = self.Config.sudo().create({"company_id": self.company_b.id})
        forbidden = config_b.with_user(self.manager_a).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        with self.assertRaises(AccessError):
            forbidden.action_test_dashboard_login()

    def test_successful_fresh_login_updates_all_timestamps(self):
        config = self._create_manager_config()
        before = fields.Datetime.now()
        result = AuthResult(
            success=True,
            status="authenticated",
            message="Bosta Dashboard authentication succeeded.",
            encrypted_session_state="encrypted-session-ciphertext",
            attempted_fresh_login=True,
        )
        config._apply_auth_result(result)
        self.assertGreaterEqual(config.last_login_attempt_at, before)
        self.assertGreaterEqual(config.last_successful_login_at, before)
        self.assertGreaterEqual(config.last_session_validation_at, before)
        self.assertFalse(config.last_auth_error)

    def test_valid_restored_session_updates_only_validation_timestamp(self):
        config = self._create_manager_config()
        result = AuthResult(
            success=True,
            status="authenticated",
            message="Bosta Dashboard saved session is authenticated.",
            used_existing_session=True,
        )
        config._apply_auth_result(result)
        self.assertTrue(config.last_session_validation_at)
        self.assertFalse(config.last_login_attempt_at)
        self.assertFalse(config.last_successful_login_at)

    def test_failed_fresh_login_updates_status_and_sanitized_error(self):
        config = self._create_manager_config()
        result = AuthResult(
            success=False,
            status="invalid_credentials",
            message="The Bosta Dashboard login or password was rejected.",
            attempted_fresh_login=True,
        )
        config._apply_auth_result(result)
        self.assertEqual(config.session_status, "invalid_credentials")
        self.assertTrue(config.last_login_attempt_at)
        self.assertEqual(
            config.last_auth_error,
            "The Bosta Dashboard login or password was rejected.",
        )

    def test_expired_session_is_replaced_after_successful_fresh_login(self):
        config = self._create_manager_config()
        config._apply_auth_result(
            AuthResult(
                success=True,
                status="authenticated",
                message="initial",
                encrypted_session_state="old-ciphertext",
                attempted_fresh_login=True,
            )
        )
        config._apply_auth_result(
            AuthResult(
                success=True,
                status="authenticated",
                message="replacement",
                encrypted_session_state="new-ciphertext",
                attempted_fresh_login=True,
                clear_session=True,
            )
        )
        self.assertEqual(config.encrypted_session_state, "new-ciphertext")

    def test_failed_refresh_clears_expired_session(self):
        config = self._create_manager_config()
        config._apply_auth_result(
            AuthResult(
                success=True,
                status="authenticated",
                message="initial",
                encrypted_session_state="old-ciphertext",
                attempted_fresh_login=True,
            )
        )
        config._apply_auth_result(
            AuthResult(
                success=False,
                status="invalid_credentials",
                message="rejected",
                attempted_fresh_login=True,
                clear_session=True,
            )
        )
        self.assertFalse(config.encrypted_session_state)
        self.assertFalse(config.session_configured)

    def test_manager_can_reset_session_and_keep_password(self):
        config = self._create_manager_config(integration_enabled=True)
        password_before = config.encrypted_dashboard_password
        config._apply_auth_result(
            AuthResult(
                success=True,
                status="authenticated",
                message="initial",
                encrypted_session_state="old-ciphertext",
                attempted_fresh_login=True,
            )
        )
        notification = config.action_reset_dashboard_session()
        self.assertEqual(notification["params"]["type"], "success")
        self.assertFalse(config.encrypted_session_state)
        self.assertFalse(config.session_configured)
        self.assertEqual(config.session_status, "not_configured")
        self.assertFalse(config.last_auth_error)
        self.assertEqual(config.encrypted_dashboard_password, password_before)
        self.assertTrue(config.dashboard_password_configured)
        self.assertTrue(config.integration_enabled)
        self.assertEqual(config.dashboard_login, "manager@example.com")

    def test_ordinary_user_cannot_reset_session(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        user_record = config.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        with self.assertRaises(AccessError):
            user_record.action_reset_dashboard_session()

    def test_plaintext_password_is_not_a_stored_model_value(self):
        config = self._create_manager_config()
        stored_values = config.sudo().read()[0]
        self.assertNotIn(self.password, repr(stored_values))
        self.assertFalse(self.Config._fields["dashboard_password_input"].store)

    def test_plaintext_password_is_absent_from_failure_notification_and_log(self):
        config = self._create_manager_config()
        result = AuthResult(
            success=False,
            status="unknown_error",
            message="Bosta Dashboard authentication failed safely.",
            attempted_fresh_login=True,
        )
        with patch(
            "odoo.addons.bosta_integration.models.bosta_config.DashboardAuthService"
        ) as service_class, patch(
            "odoo.addons.bosta_integration.models.bosta_config._logger.warning"
        ) as warning:
            service_class.return_value.authenticate.return_value = result
            notification = config.action_test_dashboard_login()
        rendered = repr(notification) + repr(warning.call_args)
        self.assertNotIn(self.password, rendered)
        self.assertNotIn(config.encrypted_dashboard_password, rendered)

    def test_encrypted_fields_are_absent_from_configuration_view(self):
        module_path = Path(get_module_path("bosta_integration"))
        view_text = (module_path / "views" / "bosta_config_views.xml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('name="encrypted_dashboard_password"', view_text)
        self.assertNotIn('name="encrypted_session_state"', view_text)

    def test_manifest_declares_playwright_without_legacy_dependency(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["depends"], ["base"])
        self.assertIn("cryptography", manifest["external_dependencies"]["python"])
        self.assertIn("playwright", manifest["external_dependencies"]["python"])
        self.assertNotIn("bosta_orders", manifest["depends"])
