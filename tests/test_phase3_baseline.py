from pathlib import Path
from unittest.mock import patch

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase


class TestBostaPhase3Baseline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env["bosta.delivery"]
        cls.company = cls.env["res.company"].create({"name": "Bosta Phase 3 Boundary"})

    def _values(self):
        return {
            "company_id": self.company.id,
            "bosta_delivery_id": "phase3-boundary-delivery",
            "tracking_number": "phase3-boundary-tracking",
        }

    def test_manifest_is_phase3_and_stays_independent(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.5.0.0")
        self.assertEqual(manifest.get("depends"), ["base"])
        for forbidden in ("sale", "stock", "account", "website"):
            self.assertNotIn(forbidden, manifest.get("depends", []))

    def test_models_do_not_store_full_raw_payload(self):
        forbidden = {
            "raw_payload",
            "raw_json",
            "api_payload",
            "search_payload",
            "details_payload",
        }
        self.assertFalse(forbidden.intersection(self.Delivery._fields))

    def test_create_and_write_perform_zero_http_requests(self):
        with patch(
            "odoo.addons.bosta_integration.services.bosta_api_client.requests.request",
            side_effect=AssertionError("Phase 3 model persistence must not perform HTTP requests"),
        ):
            delivery = self.Delivery.create(self._values())
            delivery.write({"state_value": "Processing"})
        self.assertEqual(delivery.state_value, "Processing")

    def test_create_does_not_create_partner_or_business_documents(self):
        watched_models = ("res.partner", "sale.order", "stock.move", "account.move")
        counts = {}
        for model_name in watched_models:
            model = self.env.registry.get(model_name)
            if model is not None:
                counts[model_name] = self.env[model_name].sudo().search_count([])

        self.Delivery.create(self._values())

        for model_name, before in counts.items():
            with self.subTest(model=model_name):
                after = self.env[model_name].sudo().search_count([])
                self.assertEqual(before, after)

    def test_phase3_introduces_no_cron_or_sync_button(self):
        module_path = Path(get_module_path("bosta_integration"))
        xml_text = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in sorted((module_path / "views").glob("*.xml"))
        )
        security_text = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in sorted((module_path / "security").glob("*.xml"))
        )
        self.assertNotIn('model="ir.cron"', xml_text + security_text)
        self.assertNotIn('string="sync', xml_text)
        self.assertNotIn('name="action_sync', xml_text)

    def test_phase3_runtime_contains_no_sale_stock_profit_or_sync_logic(self):
        module_path = Path(get_module_path("bosta_integration"))
        phase3_models = (
            module_path / "models" / "bosta_delivery.py",
            module_path / "models" / "bosta_delivery_item.py",
        )
        rendered = "\n".join(path.read_text(encoding="utf-8").lower() for path in phase3_models)
        for forbidden in (
            'env["sale.order"]',
            "env['sale.order']",
            'env["stock.move"]',
            "env['stock.move']",
            'env["res.partner"]',
            "env['res.partner']",
            "requests.",
            "bostaapiclient(",
            "profit",
            "settlement",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)
