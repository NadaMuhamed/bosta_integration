from pathlib import Path

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase

from ..services.bosta_lifecycle_interpreter import (
    LIFECYCLE_STAGES,
    RETURN_SCENARIOS,
    BostaLifecycleInterpreter,
)


class TestBostaPhase6Baseline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env["bosta.delivery"]
        cls.module_path = Path(get_module_path("bosta_integration"))

    def test_01_manifest_version_dependency_and_installability(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.9.0.0")
        self.assertEqual(manifest.get("depends"), ["base", "stock"])
        self.assertTrue(manifest.get("installable"))

    def test_02_exact_lifecycle_stage_values_exist(self):
        expected = {
            "unknown", "pre_pickup", "with_bosta", "delivered_to_customer",
            "returning_to_origin", "returned_to_origin", "customer_return_pickup",
            "customer_return_completed", "terminated", "lost", "damaged", "ambiguous",
        }
        self.assertEqual(set(LIFECYCLE_STAGES), expected)
        model_values = {value for value, _label in self.Delivery._fields["lifecycle_stage"].selection}
        self.assertEqual(model_values, expected)

    def test_03_exact_return_scenario_values_exist(self):
        expected = {
            "none", "pre_delivery_return", "post_delivery_customer_return",
            "partial_return", "lost", "damaged", "ambiguous",
        }
        self.assertEqual(set(RETURN_SCENARIOS), expected)
        model_values = {value for value, _label in self.Delivery._fields["return_scenario"].selection}
        self.assertEqual(model_values, expected)

    def test_04_lifecycle_fields_are_persisted_but_timeline_is_not(self):
        for field_name in (
            "lifecycle_stage", "return_scenario", "lifecycle_rule_code", "lifecycle_ambiguous"
        ):
            self.assertIn(field_name, self.Delivery._fields)
        for forbidden in ("raw_payload", "raw_json", "timeline", "timeline_json", "raw_timeline"):
            self.assertNotIn(forbidden, self.Delivery._fields)

    def test_05_interpreter_is_pure_no_orm_http_or_credentials(self):
        path = self.module_path / "services" / "bosta_lifecycle_interpreter.py"
        rendered = path.read_text(encoding="utf-8").lower()
        for forbidden in (
            'env[', '.create(', '.write(', '.unlink(', 'requests.', 'transport.request',
            'authorization', 'api_key', 'stock.move', 'sale.order', 'res.partner',
            'product.product', 'account.move', 'ir.cron',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)
        self.assertTrue(BostaLifecycleInterpreter())

    def test_06_phase6_runtime_has_no_business_document_or_stock_side_effect_code(self):
        runtime = "\n".join(
            (self.module_path / relative).read_text(encoding="utf-8").lower()
            for relative in (
                "services/bosta_lifecycle_interpreter.py",
                "services/bosta_persistence_service.py",
            )
        )
        forbidden = (
            'env["res.partner"]', "env['res.partner']", 'env["sale.order"]', "env['sale.order']",
            'env["product.product"]', "env['product.product']", 'env["stock.move"]', "env['stock.move']",
            'env["account.move"]', "env['account.move']", "stock.quant", "stock.picking",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, runtime)

    def test_07_no_cron_added(self):
        xml = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in sorted(self.module_path.rglob("*.xml"))
        )
        self.assertNotIn('model="ir.cron"', xml)
        self.assertNotIn("interval_number", xml)
        self.assertNotIn("nextcall", xml)

    def test_08_search_path_has_no_implicit_details_call(self):
        extraction = (self.module_path / "services" / "bosta_extraction_service.py").read_text(encoding="utf-8")
        method = extraction.split("def iter_normalized_search_deliveries", 1)[1].split("def get_normalized_delivery_details", 1)[0]
        self.assertNotIn("get_delivery_details", method)

    def test_09_views_expose_lifecycle_inspection_fields(self):
        view = (self.module_path / "views" / "bosta_delivery_views.xml").read_text(encoding="utf-8")
        for field_name in (
            "flow_type", "state_value", "lifecycle_stage", "return_scenario",
            "lifecycle_ambiguous", "lifecycle_rule_code",
        ):
            self.assertIn(f'name="{field_name}"', view)

    def test_10_phase7_adds_only_stock_dependency_not_later_business_apps(self):
        manifest = get_manifest("bosta_integration")
        self.assertIn("stock", manifest.get("depends", []))
        self.assertNotIn("sale", manifest.get("depends", []))
        self.assertNotIn("account", manifest.get("depends", []))
        self.assertNotIn("purchase", manifest.get("depends", []))
        self.assertNotIn("website", manifest.get("depends", []))
