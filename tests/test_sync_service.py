from unittest.mock import patch

from odoo import api
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase

from ..services.exceptions import BostaApiTimeoutError


SECRET = "phase5-synthetic-secret-never-log"
PII = "Synthetic Receiver 01000000000"


class TestBostaManualSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Sync A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Sync B"})
        cls.manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager = cls._user("phase5-manager", cls.company_a, [cls.company_a, cls.company_b], cls.manager_group)
        cls.ordinary = cls._user("phase5-user", cls.company_a, [cls.company_a], cls.user_group)

    @classmethod
    def _user(cls, login, company, companies, group):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.invalid",
            "company_id": company.id,
            "company_ids": [(6, 0, [item.id for item in companies])],
            "groups_id": [(6, 0, [group.id])],
        })

    def _config(self, company=None, enabled=True):
        company = company or self.company_a
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True):
            return self.Config.sudo().create({
                "company_id": company.id,
                "integration_enabled": enabled,
                "page_size": 200,
                "max_pages": 10,
            })

    def _manager_config(self, config):
        return config.with_user(self.manager).with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id]
        )

    @staticmethod
    def _fill_summary(_extraction, _company, **kwargs):
        summary = kwargs["summary"]
        summary.update({
            "seen": 6,
            "created": 3,
            "updated": 1,
            "unchanged": 2,
            "conflicts": 0,
            "errors": 0,
        })
        return summary

    def test_01_ordinary_user_cannot_run_sync_action(self):
        config = self._config().with_user(self.ordinary).with_context(allowed_company_ids=[self.company_a.id])
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), self.assertRaises(AccessError):
            config.action_sync_bosta_deliveries()

    def test_02_manager_can_run_sync_action(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaPersistenceService") as persistence_class, \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_release_sync_lock"):
            persistence_class.empty_summary.return_value = {
                "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
            }
            persistence_class.return_value.persist_search_deliveries.side_effect = self._fill_summary
            notification = config.action_sync_bosta_deliveries()
        self.assertEqual(notification["params"]["type"], "success")
        self.assertEqual(config.last_sync_status, "success")

    def test_03_disabled_integration_cannot_sync(self):
        config = self._manager_config(self._config(enabled=False))
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), self.assertRaises(UserError):
            config.action_sync_bosta_deliveries()

    def test_04_missing_api_key_fails_before_client_or_http(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(type(config), "_build_api_client") as build_client, \
             self.assertRaises(UserError):
            config.action_sync_bosta_deliveries()
        build_client.assert_not_called()

    def test_05_successful_sync_updates_safe_audit_fields_and_counts(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaPersistenceService") as persistence_class, \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_release_sync_lock"):
            persistence_class.empty_summary.return_value = {
                "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
            }
            persistence_class.return_value.persist_search_deliveries.side_effect = self._fill_summary
            notification = config.action_sync_bosta_deliveries()
        self.assertTrue(config.last_sync_started_at)
        self.assertTrue(config.last_sync_completed_at)
        self.assertEqual(config.last_sync_status, "success")
        self.assertEqual(config.last_sync_seen_count, 6)
        self.assertEqual(config.last_sync_created_count, 3)
        self.assertEqual(config.last_sync_updated_count, 1)
        self.assertEqual(config.last_sync_unchanged_count, 2)
        self.assertEqual(config.last_sync_conflict_count, 0)
        self.assertEqual(config.last_sync_error_count, 0)
        self.assertFalse(config.last_sync_error)
        self.assertIn("Seen: 6", notification["params"]["message"])

    def test_06_partial_sync_records_conflict_and_error_counts(self):
        config = self._manager_config(self._config())

        def partial(_extraction, _company, **kwargs):
            kwargs["summary"].update({
                "seen": 5, "created": 2, "updated": 1, "unchanged": 0, "conflicts": 1, "errors": 1,
            })

        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaPersistenceService") as persistence_class, \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_release_sync_lock"):
            persistence_class.empty_summary.return_value = {
                "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
            }
            persistence_class.return_value.persist_search_deliveries.side_effect = partial
            notification = config.action_sync_bosta_deliveries()
        self.assertEqual(config.last_sync_status, "partial")
        self.assertEqual(config.last_sync_conflict_count, 1)
        self.assertEqual(config.last_sync_error_count, 1)
        self.assertTrue(config.last_sync_error)
        self.assertEqual(notification["params"]["type"], "warning")

    def test_07_notification_and_audit_do_not_leak_pii_secret_or_payload(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaPersistenceService") as persistence_class, \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_release_sync_lock"):
            persistence_class.empty_summary.return_value = {
                "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
            }
            persistence_class.return_value.persist_search_deliveries.side_effect = self._fill_summary
            notification = config.action_sync_bosta_deliveries()
        rendered = repr(notification) + repr(config.last_sync_error)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(PII, rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("raw_payload", rendered)

    def test_08_api_failure_is_safe_marks_failed_and_releases_lock(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaPersistenceService") as persistence_class, \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_release_sync_lock") as release:
            persistence_class.empty_summary.return_value = {
                "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
            }
            def fail(_extraction, _company, **kwargs):
                kwargs["summary"]["seen"] = 2
                kwargs["summary"]["created"] = 2
                raise BostaApiTimeoutError()
            persistence_class.return_value.persist_search_deliveries.side_effect = fail
            notification = config.action_sync_bosta_deliveries()
        release.assert_called_once()
        self.assertEqual(config.last_sync_status, "failed")
        self.assertEqual(config.last_sync_seen_count, 2)
        self.assertEqual(config.last_sync_created_count, 2)
        self.assertEqual(notification["params"]["type"], "danger")
        self.assertNotIn(SECRET, repr(notification) + repr(config.last_sync_error))

    def test_lock_released_if_running_audit_write_fails(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=True), \
             patch.object(type(config), "_write_sync_state", side_effect=RuntimeError("synthetic audit failure")), \
             patch.object(type(config), "_release_sync_lock") as release, \
             patch.object(type(config), "_build_api_client") as build_client, \
             self.assertRaisesRegex(RuntimeError, "synthetic audit failure"):
            config.action_sync_bosta_deliveries()
        release.assert_called_once_with()
        build_client.assert_not_called()

    def test_09_concurrent_same_config_refuses_before_sync(self):
        config = self._manager_config(self._config())
        with patch.dict("os.environ", {"BOSTA_API_KEY": SECRET}, clear=True), \
             patch.object(type(config), "_try_acquire_sync_lock", return_value=False), \
             patch.object(type(config), "_build_api_client") as build_client, \
             self.assertRaises(UserError):
            config.action_sync_bosta_deliveries()
        build_client.assert_not_called()

    def test_10_lock_keys_are_config_specific(self):
        config_a = self._config(company=self.company_a)
        config_b = self._config(company=self.company_b)
        self.assertNotEqual(config_a._sync_lock_key(), config_b._sync_lock_key())

    def test_11_same_config_advisory_lock_blocks_second_database_session(self):
        config = self._config()
        self.assertTrue(config._try_acquire_sync_lock())
        try:
            with self.registry.cursor() as other_cr:
                other_env = api.Environment(other_cr, self.env.uid, {})
                other = other_env["bosta.integration.config"].sudo().browse(config.id)
                self.assertFalse(other._try_acquire_sync_lock())
        finally:
            config._release_sync_lock()

    def test_12_different_config_locks_are_independent_across_sessions(self):
        config_a = self._config(company=self.company_a)
        config_b = self._config(company=self.company_b)
        self.assertTrue(config_a._try_acquire_sync_lock())
        try:
            with self.registry.cursor() as other_cr:
                other_env = api.Environment(other_cr, self.env.uid, {})
                other = other_env["bosta.integration.config"].sudo().browse(config_b.id)
                self.assertTrue(other._try_acquire_sync_lock())
                other._release_sync_lock()
        finally:
            config_a._release_sync_lock()

    def test_13_lock_can_be_reacquired_after_release(self):
        config = self._config()
        self.assertTrue(config._try_acquire_sync_lock())
        config._release_sync_lock()
        with self.registry.cursor() as other_cr:
            other_env = api.Environment(other_cr, self.env.uid, {})
            other = other_env["bosta.integration.config"].sudo().browse(config.id)
            self.assertTrue(other._try_acquire_sync_lock())
            other._release_sync_lock()

    def test_14_sync_audit_fields_are_system_managed(self):
        config = self._config(enabled=False)
        protected = {
            "last_sync_status": "success",
            "last_sync_seen_count": 999,
            "last_sync_error": "forbidden",
        }
        for field_name, value in protected.items():
            with self.subTest(field=field_name), self.assertRaises(AccessError):
                config.write({field_name: value})

    def test_15_view_action_is_manager_only_and_named_explicitly(self):
        view = self.env.ref("bosta_integration.view_bosta_integration_config_form")
        arch = view.arch_db
        self.assertIn("action_sync_bosta_deliveries", arch)
        self.assertIn("Sync Bosta Deliveries", arch)
        self.assertIn("bosta_integration.group_bosta_integration_manager", arch)
