from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestBostaApiConfiguration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]

    def _company(self, suffix):
        return self.env["res.company"].create({"name": f"Bosta API Config {suffix}"})

    def test_defaults_and_model_registration(self):
        company = self._company("defaults")
        config = self.Config.sudo().create({"company_id": company.id})
        self.assertEqual(self.Config._name, "bosta.integration.config")
        self.assertEqual(config.api_base_url, "https://app.bosta.co")
        self.assertEqual(config.api_key_env_var, "BOSTA_API_KEY")
        self.assertEqual(config.request_timeout_seconds, 30)
        self.assertEqual(config.page_size, 1500)
        self.assertEqual(config.max_pages, 10000)
        self.assertEqual(config.api_status, "not_configured")

    def test_page_size_bounds(self):
        for index, value in enumerate((0, -1, 1501)):
            company = self._company(f"page-{index}")
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create({"company_id": company.id, "page_size": value})

    def test_timeout_bounds(self):
        for index, value in enumerate((4, 121)):
            company = self._company(f"timeout-{index}")
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create({"company_id": company.id, "request_timeout_seconds": value})

    def test_max_pages_bounds(self):
        for index, value in enumerate((0, 10001)):
            company = self._company(f"max-pages-{index}")
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create({"company_id": company.id, "max_pages": value})

    def test_api_base_url_rejections(self):
        rejected = [
            "http://app.bosta.co",
            "https://app.bosta.co.example.com",
            "https://user:pass@app.bosta.co",
            "https://app.bosta.co:444",
            "https://example.com",
            "https://app.bosta.co/path",
            "https://app.bosta.co?x=1",
            "https://app.bosta.co#fragment",
        ]
        for index, value in enumerate(rejected):
            company = self._company(f"url-{index}")
            with self.subTest(value=value), self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create({"company_id": company.id, "api_base_url": value})

    def test_api_base_url_normalizes_trailing_slash(self):
        company = self._company("url-normalize")
        config = self.Config.sudo().create({"company_id": company.id, "api_base_url": " https://app.bosta.co/ "})
        self.assertEqual(config.api_base_url, "https://app.bosta.co")

    def test_environment_variable_name_validation(self):
        accepted = ["BOSTA_API_KEY", "BOSTA_API_KEY_COMPANY_2", "_BOSTA_KEY"]
        rejected = ["", "bosta_api_key", "2BOSTA_API_KEY", "BOSTA-API-KEY", "BOSTA API KEY"]
        for index, value in enumerate(accepted):
            company = self._company(f"env-ok-{index}")
            config = self.Config.sudo().create({"company_id": company.id, "api_key_env_var": value})
            self.assertEqual(config.api_key_env_var, value)
        for index, value in enumerate(rejected):
            company = self._company(f"env-bad-{index}")
            with self.subTest(value=value), self.assertRaises(ValidationError), self.cr.savepoint():
                self.Config.sudo().create({"company_id": company.id, "api_key_env_var": value})

    def test_api_key_configured_boolean_uses_environment_only(self):
        company = self._company("configured")
        config = self.Config.sudo().create({"company_id": company.id})
        for environment in ({}, {"BOSTA_API_KEY": ""}, {"BOSTA_API_KEY": "   "}):
            with patch.dict("os.environ", environment, clear=True):
                config.invalidate_recordset(["api_key_configured"])
                self.assertFalse(config.api_key_configured)
        with patch.dict("os.environ", {"BOSTA_API_KEY": "test-bosta-key-do-not-use"}, clear=True):
            config.invalidate_recordset(["api_key_configured"])
            self.assertTrue(config.api_key_configured)

    def test_enable_requires_environment_key(self):
        company = self._company("enable")
        config = self.Config.sudo().create({"company_id": company.id})
        for environment in ({}, {"BOSTA_API_KEY": ""}, {"BOSTA_API_KEY": "   "}):
            with patch.dict("os.environ", environment, clear=True):
                with self.assertRaises(ValidationError), self.cr.savepoint():
                    config.write({"integration_enabled": True})
        with patch.dict("os.environ", {"BOSTA_API_KEY": "test-bosta-key-do-not-use"}, clear=True):
            config.write({"integration_enabled": True})
        self.assertTrue(config.integration_enabled)

    def test_one_configuration_per_company(self):
        company = self._company("unique")
        self.Config.sudo().create({"company_id": company.id})
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Config.sudo().create({"company_id": company.id})
