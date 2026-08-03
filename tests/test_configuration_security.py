from unittest.mock import patch

from cryptography.fernet import Fernet
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..services import crypto_service


@tagged("post_install", "-at_install")
class TestBostaConfigurationSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Phase 1 A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Phase 1 B"})
        cls.user_group = cls.env.ref(
            "bosta_integration.group_bosta_integration_user"
        )
        cls.manager_group = cls.env.ref(
            "bosta_integration.group_bosta_integration_manager"
        )
        cls.manager_a = cls._create_test_user(
            "bosta_manager_a",
            cls.company_a,
            [cls.company_a],
            cls.manager_group,
        )
        cls.manager_both = cls._create_test_user(
            "bosta_manager_both",
            cls.company_a,
            [cls.company_a, cls.company_b],
            cls.manager_group,
        )
        cls.integration_user = cls._create_test_user(
            "bosta_integration_user",
            cls.company_a,
            [cls.company_a],
            cls.user_group,
        )
        cls.encryption_key = Fernet.generate_key().decode("ascii")

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

    def _manager_model(self, manager=None, companies=None):
        manager = manager or self.manager_a
        companies = companies or [self.company_a]
        return self.Config.with_user(manager).with_context(
            allowed_company_ids=[company.id for company in companies]
        )

    def _key_environment(self):
        return patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: self.encryption_key},
            clear=True,
        )

    def test_default_configuration_values(self):
        config = self.Config.with_company(self.company_a).sudo().create(
            {"company_id": self.company_a.id}
        )
        self.assertEqual(config.name, "Bosta Integration")
        self.assertEqual(config.company_id, self.company_a)
        self.assertEqual(config.dashboard_url, "https://business.bosta.co/orders")
        self.assertFalse(config.integration_enabled)
        self.assertFalse(config.dashboard_password_configured)

    def test_one_configuration_per_company(self):
        self.Config.sudo().create({"company_id": self.company_a.id})
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Config.sudo().create({"company_id": self.company_a.id})

    def test_one_configuration_for_each_different_company(self):
        config_a = self.Config.sudo().create({"company_id": self.company_a.id})
        config_b = self.Config.sudo().create({"company_id": self.company_b.id})
        self.assertTrue(config_a)
        self.assertTrue(config_b)
        self.assertNotEqual(config_a.company_id, config_b.company_id)

    def test_dashboard_url_validation(self):
        accepted = [
            "https://business.bosta.co/orders",
            "https://business.bosta.co/orders/details",
            "https://business.bosta.co:443/orders",
        ]
        for index, url in enumerate(accepted):
            company = self.env["res.company"].create(
                {"name": f"Accepted URL Company {index}"}
            )
            config = self.Config.sudo().create(
                {"company_id": company.id, "dashboard_url": url}
            )
            self.assertEqual(config.dashboard_url, url)

        rejected = [
            "http://business.bosta.co/orders",
            "https://example.com/orders",
            "https://localhost/orders",
            "https://127.0.0.1/orders",
            "file:///orders",
            "javascript:alert(1)",
            "not a url",
            "https://business.bosta.co/login",
        ]
        for index, url in enumerate(rejected):
            company = self.env["res.company"].create(
                {"name": f"Rejected URL Company {index}"}
            )
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create(
                    {"company_id": company.id, "dashboard_url": url}
                )

    def test_password_is_encrypted_and_input_is_not_stored(self):
        plaintext = "correct-horse-battery-staple"
        with self._key_environment():
            config = self.Config.sudo().create(
                {
                    "company_id": self.company_a.id,
                    "dashboard_login": "  USER@EXAMPLE.COM  ",
                    "dashboard_password_input": plaintext,
                }
            )
            decrypted = crypto_service.decrypt_secret(
                config.encrypted_dashboard_password
            )

        self.assertNotEqual(config.encrypted_dashboard_password, plaintext)
        self.assertEqual(decrypted, plaintext)
        self.assertEqual(config.dashboard_login, "user@example.com")
        self.assertFalse(config.dashboard_password_input)
        self.assertFalse(self.Config._fields["dashboard_password_input"].store)
        self.assertTrue(config.dashboard_password_configured)

        self.cr.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = %s
            """,
            [self.Config._table],
        )
        column_names = {row[0] for row in self.cr.fetchall()}
        self.assertNotIn("dashboard_password_input", column_names)

    def test_missing_or_invalid_key_does_not_leak_password(self):
        password = "private-password-value"
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(UserError) as missing_error:
                self.Config.sudo().create(
                    {
                        "company_id": self.company_a.id,
                        "dashboard_password_input": password,
                    }
                )
        self.assertNotIn(password, str(missing_error.exception))

        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: "invalid-key-value"},
            clear=True,
        ):
            with self.assertRaises(UserError) as invalid_error:
                self.Config.sudo().create(
                    {
                        "company_id": self.company_a.id,
                        "dashboard_password_input": password,
                    }
                )
        self.assertNotIn(password, str(invalid_error.exception))
        self.assertNotIn("invalid-key-value", str(invalid_error.exception))

    def test_blank_password_update_keeps_saved_password(self):
        with self._key_environment():
            config = self.Config.sudo().create(
                {
                    "company_id": self.company_a.id,
                    "dashboard_password_input": "original-password",
                }
            )
            encrypted_before = config.encrypted_dashboard_password
            updated_at_before = config.credentials_updated_at
            config.write({"dashboard_password_input": ""})

        self.assertEqual(config.encrypted_dashboard_password, encrypted_before)
        self.assertEqual(config.credentials_updated_at, updated_at_before)

    def test_clear_password_disables_integration_and_updates_audit(self):
        model = self._manager_model()
        with self._key_environment():
            config = model.create(
                {
                    "company_id": self.company_a.id,
                    "dashboard_login": "manager@example.com",
                    "dashboard_password_input": "configured-password",
                    "integration_enabled": True,
                }
            )
            self.assertTrue(config.dashboard_password_configured)
            self.assertTrue(config.integration_enabled)
            config.action_clear_saved_password()

        self.assertFalse(config.encrypted_dashboard_password)
        self.assertFalse(config.dashboard_password_configured)
        self.assertFalse(config.integration_enabled)
        self.assertTrue(config.credentials_updated_at)
        self.assertEqual(config.credentials_updated_by, self.manager_a)

    def test_enable_requires_complete_credentials(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})

        with self.assertRaises(ValidationError), self.cr.savepoint():
            config.write({"integration_enabled": True})

        config.write({"dashboard_login": "201000000000"})
        self.assertEqual(config.dashboard_login, "201000000000")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            config.write({"integration_enabled": True})

        with self._key_environment():
            config.write({"dashboard_password_input": "configured-password"})
            config.write({"integration_enabled": True})
        self.assertTrue(config.integration_enabled)

    def test_manager_has_crud_access_for_allowed_company(self):
        model = self._manager_model()
        config = model.create(
            {"company_id": self.company_a.id, "name": "Manager Configuration"}
        )
        self.assertEqual(config.read(["name"])[0]["name"], "Manager Configuration")
        config.write({"name": "Updated Manager Configuration"})
        self.assertEqual(config.name, "Updated Manager Configuration")
        config.unlink()
        self.assertFalse(config.exists())

    def test_ordinary_integration_user_has_no_configuration_access(self):
        model = self.Config.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        with self.assertRaises(AccessError):
            model.check_access_rights("read")
        with self.assertRaises(AccessError):
            model.search([])

    def test_multi_company_record_isolation(self):
        config_a = self.Config.sudo().create({"company_id": self.company_a.id})
        config_b = self.Config.sudo().create({"company_id": self.company_b.id})

        manager_a_model = self._manager_model(self.manager_a, [self.company_a])
        visible_to_a = manager_a_model.search([])
        self.assertIn(config_a.id, visible_to_a.ids)
        self.assertNotIn(config_b.id, visible_to_a.ids)

        with self.assertRaises(AccessError), self.cr.savepoint():
            config_b.with_user(self.manager_a).with_context(
                allowed_company_ids=[self.company_a.id]
            ).write({"name": "Forbidden"})

        with self.assertRaises(AccessError), self.cr.savepoint():
            config_b.with_user(self.manager_a).with_context(
                allowed_company_ids=[self.company_a.id]
            ).unlink()

        manager_both_model = self._manager_model(
            self.manager_both,
            [self.company_a, self.company_b],
        )
        visible_to_both = manager_both_model.search([])
        self.assertIn(config_a.id, visible_to_both.ids)
        self.assertIn(config_b.id, visible_to_both.ids)

    def test_security_groups_exist_and_manager_implies_user(self):
        self.assertTrue(self.user_group.exists())
        self.assertTrue(self.manager_group.exists())
        self.assertIn(self.user_group, self.manager_group.implied_ids)

        model_id = self.env["ir.model"]._get_id("bosta.integration.config")
        access_records = self.env["ir.model.access"].sudo().search(
            [("model_id", "=", model_id)]
        )
        self.assertEqual(access_records.mapped("group_id"), self.manager_group)

    def test_direct_secret_and_audit_writes_are_rejected(self):
        config = self.Config.sudo().create({"company_id": self.company_a.id})
        for vals in (
            {"encrypted_dashboard_password": "not-allowed"},
            {"credentials_updated_at": fields.Datetime.now()},
            {"credentials_updated_by": self.env.user.id},
        ):
            with self.assertRaises(AccessError):
                config.write(vals)

        with self.assertRaises(AccessError):
            config.with_context(_bosta_internal_secret_write=True).write(
                {"encrypted_dashboard_password": "spoofed-context-value"}
            )

    def test_errors_do_not_expose_secret_values(self):
        password = "secret-value-for-redaction-test"
        key = "invalid-key-for-redaction-test"
        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: key},
            clear=True,
        ):
            with self.assertRaises(UserError) as caught:
                self.Config.sudo().create(
                    {
                        "company_id": self.company_a.id,
                        "dashboard_password_input": password,
                    }
                )

        message = str(caught.exception)
        self.assertNotIn(password, message)
        self.assertNotIn(key, message)
