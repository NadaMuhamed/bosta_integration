from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase

from ..services.exceptions import (
    BostaApiAuthenticationError,
    BostaApiContractError,
    BostaApiPermissionError,
    BostaApiTimeoutError,
)


SECRET = "test-bosta-key-do-not-use"
LOGGER_NAME = "odoo.addons.bosta_integration.models.bosta_config"


class TestBostaApiActions(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.company = cls.env["res.company"].create({"name": "Bosta API Actions"})
        cls.manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager = cls._user("bosta_api_manager", cls.manager_group)
        cls.user = cls._user("bosta_api_user", cls.user_group)

    @classmethod
    def _user(cls, login, group):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [group.id])],
        })

    def _config(self):
        return self.Config.sudo().create({"company_id": self.company.id}).with_user(self.manager).with_context(allowed_company_ids=[self.company.id])

    def test_manager_success_updates_state(self):
        config = self._config()
        before = fields.Datetime.now()
        with patch("odoo.addons.bosta_integration.models.bosta_config.BostaApiClient") as client_class:
            client_class.return_value.test_connection.return_value = []
            notification = config.action_test_api_connection()
        self.assertEqual(notification["params"]["type"], "success")
        self.assertEqual(config.api_status, "connected")
        self.assertGreaterEqual(config.last_api_test_at, before)
        self.assertGreaterEqual(config.last_successful_api_request_at, before)
        self.assertFalse(config.last_api_error)

    def test_ordinary_user_cannot_invoke_action(self):
        config = self.Config.sudo().create({"company_id": self.company.id})
        with self.assertRaises(AccessError):
            config.with_user(self.user).with_context(allowed_company_ids=[self.company.id]).action_test_api_connection()

    def test_failure_statuses_and_actual_logs_are_safe(self):
        cases = [
            (BostaApiAuthenticationError(f"Authorization: {SECRET} secret-response-body"), "authentication_failed"),
            (BostaApiPermissionError(f"Authorization: {SECRET} secret-response-body"), "permission_denied"),
            (BostaApiTimeoutError(f"Authorization: {SECRET} secret-response-body"), "timeout"),
            (BostaApiContractError(f"Authorization: {SECRET} secret-response-body"), "contract_error"),
        ]
        for index, (error, expected_status) in enumerate(cases):
            company = self.env["res.company"].create({"name": f"Bosta Action Failure {index}"})
            manager = self.env["res.users"].with_context(no_reset_password=True).create({
                "name": f"manager-{index}",
                "login": f"manager-{index}",
                "company_id": company.id,
                "company_ids": [(6, 0, [company.id])],
                "groups_id": [(6, 0, [self.manager_group.id])],
            })
            config = self.Config.sudo().create({"company_id": company.id}).with_user(manager).with_context(allowed_company_ids=[company.id])
            with patch("odoo.addons.bosta_integration.models.bosta_config.BostaApiClient") as client_class:
                client_class.return_value.test_connection.side_effect = error
                with self.assertLogs(LOGGER_NAME, level="WARNING") as captured:
                    notification = config.action_test_api_connection()
            rendered = repr(notification) + repr(config.last_api_error) + "\n".join(captured.output)
            self.assertEqual(config.api_status, expected_status)
            self.assertTrue(config.last_api_test_at)
            self.assertTrue(config.last_api_error)
            self.assertNotIn("Authorization", rendered)
            self.assertNotIn(SECRET, rendered)
            self.assertNotIn("secret-response-body", rendered)

    def test_unexpected_exception_is_fully_redacted(self):
        config = self._config()
        with patch("odoo.addons.bosta_integration.models.bosta_config.BostaApiClient") as client_class:
            client_class.return_value.test_connection.side_effect = RuntimeError(
                f"Authorization: {SECRET} secret-response-body"
            )
            with self.assertLogs(LOGGER_NAME, level="ERROR") as captured:
                notification = config.action_test_api_connection()
        rendered = repr(notification) + repr(config.last_api_error) + "\n".join(captured.output)
        self.assertEqual(config.api_status, "unknown_error")
        self.assertEqual(config.last_api_error, config._safe_status_message("unknown_error"))
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("secret-response-body", rendered)

    def test_failed_test_preserves_last_success_timestamp(self):
        config = self._config()
        previous_success = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        config._write_api_state({"last_successful_api_request_at": previous_success})
        with patch("odoo.addons.bosta_integration.models.bosta_config.BostaApiClient") as client_class:
            client_class.return_value.test_connection.side_effect = BostaApiTimeoutError()
            config.action_test_api_connection()
        self.assertEqual(config.api_status, "timeout")
        self.assertEqual(config.last_successful_api_request_at, previous_success)
        self.assertTrue(config.last_api_test_at)
        self.assertTrue(config.last_api_error)

    def test_failure_followed_by_success_resets_safe_state(self):
        config = self._config()
        with patch("odoo.addons.bosta_integration.models.bosta_config.BostaApiClient") as client_class:
            client_class.return_value.test_connection.side_effect = BostaApiAuthenticationError()
            config.action_test_api_connection()
            failed_test_at = config.last_api_test_at
            self.assertEqual(config.api_status, "authentication_failed")
            self.assertTrue(config.last_api_error)

            client_class.return_value.test_connection.side_effect = None
            client_class.return_value.test_connection.return_value = []
            notification = config.action_test_api_connection()

        self.assertEqual(notification["params"]["type"], "success")
        self.assertEqual(config.api_status, "connected")
        self.assertFalse(config.last_api_error)
        self.assertGreaterEqual(config.last_api_test_at, failed_test_at)
        self.assertTrue(config.last_successful_api_request_at)
