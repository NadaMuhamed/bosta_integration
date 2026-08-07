from pathlib import Path
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase


class TestBostaConfigurationSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Security A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Security B"})
        cls.manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager_a = cls._user("manager-a", cls.company_a, [cls.company_a], cls.manager_group)
        cls.manager_both = cls._user("manager-both", cls.company_a, [cls.company_a, cls.company_b], cls.manager_group)
        cls.integration_user = cls._user("integration-user", cls.company_a, [cls.company_a], cls.user_group)

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

    def test_no_api_secret_field_or_database_storage(self):
        self.assertNotIn("api_key", self.Config._fields)
        self.assertFalse(self.Config._fields["api_key_configured"].store)
        company = self.env["res.company"].create({"name": "Bosta Secret Storage"})
        secret = "test-bosta-key-do-not-use"
        with patch.dict("os.environ", {"BOSTA_API_KEY": secret}, clear=True):
            config = self.Config.sudo().create({"company_id": company.id})
            values = config.read()[0]
        self.assertNotIn(secret, repr(values))
        self.cr.execute("SELECT * FROM bosta_integration_config WHERE id = %s", [config.id])
        self.assertNotIn(secret, repr(self.cr.fetchone()))

    def test_api_state_fields_are_not_directly_writable_or_creatable(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        protected = ("api_status", "last_api_test_at", "last_successful_api_request_at", "last_api_error")
        for field_name in protected:
            with self.subTest(operation="write", field=field_name), self.assertRaises(AccessError):
                config.write({field_name: "connected" if field_name == "api_status" else "forbidden"})

        for index, field_name in enumerate(protected):
            company = self.env["res.company"].create({"name": f"Bosta Protected Create {index}"})
            with self.subTest(operation="create", field=field_name), self.assertRaises(AccessError):
                self.Config.sudo().create({
                    "company_id": company.id,
                    field_name: "connected" if field_name == "api_status" else "forbidden",
                })

    def test_manager_full_crud_and_multi_company_isolation(self):
        model_a = self.Config.with_user(self.manager_a).with_context(allowed_company_ids=[self.company_a.id])
        config_a = model_a.create({"company_id": self.company_a.id, "name": "A"})
        self.assertEqual(config_a.read(["name"])[0]["name"], "A")
        config_a.write({"name": "A2"})
        self.assertEqual(config_a.name, "A2")

        config_b = self.Config.sudo().create({"company_id": self.company_b.id})
        self.assertNotIn(config_b, model_a.search([]))
        with self.assertRaises(AccessError):
            config_b.with_user(self.manager_a).with_context(allowed_company_ids=[self.company_a.id]).write({"name": "forbidden"})

        both = self.Config.with_user(self.manager_both).with_context(allowed_company_ids=[self.company_a.id, self.company_b.id]).search([])
        self.assertIn(config_a, both)
        self.assertIn(config_b, both)

        delete_company = self.env["res.company"].create({"name": "Bosta Manager Delete"})
        manager_delete = self._user("manager-delete", delete_company, [delete_company], self.manager_group)
        delete_config = self.Config.with_user(manager_delete).with_context(allowed_company_ids=[delete_company.id]).create({"company_id": delete_company.id})
        self.assertTrue(delete_config.unlink())

    def test_ordinary_user_cannot_delete_configuration(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        with self.assertRaises(AccessError):
            config.with_user(self.integration_user).with_context(allowed_company_ids=[self.company_a.id]).unlink()

    def test_cross_company_create_is_blocked(self):
        model_a = self.Config.with_user(self.manager_a).with_context(allowed_company_ids=[self.company_a.id])
        with self.assertRaises(AccessError):
            model_a.create({"company_id": self.company_b.id, "name": "forbidden cross-company create"})

    def test_ordinary_integration_user_has_no_config_access(self):
        model = self.Config.with_user(self.integration_user).with_context(allowed_company_ids=[self.company_a.id])
        with self.assertRaises(AccessError):
            model.search([])

    def test_configuration_view_never_contains_secret_or_dashboard_inputs(self):
        module_path = Path(get_module_path("bosta_integration"))
        view = (module_path / "views" / "bosta_config_views.xml").read_text(encoding="utf-8")
        self.assertNotIn('name="api_key"', view)
        self.assertNotIn('password="True"', view)
        for forbidden in (
            "dashboard_url", "dashboard_login", "dashboard_password_input",
            "encrypted_dashboard_password", "encrypted_session_state", "session_status",
            "browser_timeout_seconds", "action_test_dashboard_login",
            "action_reset_dashboard_session", "Test Login", "Reset Dashboard Session",
            "Clear Saved Password",
        ):
            self.assertNotIn(forbidden, view)
        for required in ("action_test_api_connection", "Test API Connection", "api_key_configured", "api_status"):
            self.assertIn(required, view)
