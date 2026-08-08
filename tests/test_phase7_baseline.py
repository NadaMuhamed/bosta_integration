from pathlib import Path

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase


class TestBostaPhase7Baseline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module_path = Path(get_module_path("bosta_integration"))

    def test_manifest_version_dependency_and_installability(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.9.0.0")
        self.assertEqual(manifest.get("depends"), ["base", "stock"])
        self.assertTrue(manifest.get("installable"))
        for forbidden in ("sale", "account", "purchase", "website"):
            self.assertNotIn(forbidden, manifest.get("depends", []))

    def test_phase7_models_are_registered(self):
        self.assertEqual(self.env["bosta.product.mapping"]._name, "bosta.product.mapping")
        self.assertEqual(self.env["bosta.inventory.effect"]._name, "bosta.inventory.effect")
        self.assertEqual(self.env["bosta.inventory.effect.line"]._name, "bosta.inventory.effect.line")

    def test_product_relation_fields_exist(self):
        Product = self.env["product.product"]
        for field_name in ("bosta_product_role", "bosta_tester_required", "bosta_tester_product_id"):
            self.assertIn(field_name, Product._fields)

    def test_inventory_config_is_opt_in_and_has_cutoff_locations(self):
        Config = self.env["bosta.integration.config"]
        for field_name in (
            "inventory_sync_enabled", "inventory_effective_from",
            "stock_source_location_id", "bosta_transit_location_id",
        ):
            self.assertIn(field_name, Config._fields)
        defaults = Config.default_get(["inventory_sync_enabled"])
        self.assertIs(defaults.get("inventory_sync_enabled", False), False)

    def test_no_direct_stock_quant_runtime_access(self):
        rendered = "\n".join(
            p.read_text(encoding="utf-8").lower()
            for folder in ("models", "services")
            for p in sorted((self.module_path / folder).glob("*.py"))
        )
        self.assertNotIn('env["stock.quant"]', rendered)
        self.assertNotIn("env['stock.quant']", rendered)
        self.assertNotIn("stock.quant.quantity", rendered)

    def test_no_phase8_business_documents_or_customer_matching(self):
        runtime = "\n".join(
            p.read_text(encoding="utf-8").lower()
            for folder in ("models", "services")
            for p in sorted((self.module_path / folder).glob("*.py"))
        )
        for forbidden in (
            'env["sale.order"]', "env['sale.order']",
            'env["sale.order.line"]', "env['sale.order.line']",
            'env["account.move"]', "env['account.move']",
            'env["res.partner"]', "env['res.partner']",
            "net_profit", "settlement",
        ):
            self.assertNotIn(forbidden, runtime)

    def test_no_cron_or_webhook_added(self):
        xml = "\n".join(p.read_text(encoding="utf-8").lower() for p in self.module_path.rglob("*.xml"))
        self.assertNotIn('model="ir.cron"', xml)
        self.assertNotIn("webhook", xml)

    def test_no_raw_payload_or_timeline_storage(self):
        Delivery = self.env["bosta.delivery"]
        for forbidden in ("raw_payload", "raw_json", "timeline", "timeline_json", "raw_timeline"):
            self.assertNotIn(forbidden, Delivery._fields)

    def test_inventory_effect_contains_no_receiver_pii_fields(self):
        Effect = self.env["bosta.inventory.effect"]
        Line = self.env["bosta.inventory.effect.line"]
        forbidden = {"receiver_name", "receiver_phone", "address", "dropoff_address", "api_key", "raw_payload"}
        self.assertFalse(forbidden.intersection(Effect._fields))
        self.assertFalse(forbidden.intersection(Line._fields))

    def test_package_description_is_persisted_as_evidence_not_fake_item(self):
        self.assertIn("package_description", self.env["bosta.delivery"]._fields)
        parser = (self.module_path / "services" / "bosta_product_code_parser.py").read_text(encoding="utf-8")
        self.assertNotIn('env["bosta.delivery.item"]', parser)

    def test_phase7_runtime_contains_no_return_restoration_engine(self):
        inventory = (self.module_path / "services" / "bosta_inventory_service.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("restore_to_source", inventory)
        self.assertNotIn("return_picking", inventory)
        self.assertIn("phase 8", inventory)
