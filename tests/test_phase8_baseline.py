import ast
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class TestPhase8Baseline(TestCase):
    def test_01_manifest_version_dependencies_installable(self):
        manifest = ast.literal_eval((ROOT / "__manifest__.py").read_text())
        self.assertEqual(manifest["version"], "18.0.10.0.0")
        self.assertEqual(manifest["depends"], ["base", "stock"])
        self.assertTrue(manifest["installable"])

    def test_02_return_model_and_service_are_loaded(self):
        self.assertIn("bosta_return_case", (ROOT / "models" / "__init__.py").read_text())
        self.assertIn("bosta_return_service", (ROOT / "services" / "__init__.py").read_text())

    def test_03_return_service_does_not_mutate_stock_quant_directly(self):
        source = (ROOT / "services" / "bosta_return_service.py").read_text()
        self.assertNotIn('env["stock.quant"]', source)
        self.assertNotIn("_update_available_quantity", source)
        self.assertIn('env["stock.picking"]', source)

    def test_04_return_runtime_has_no_phase9_scope(self):
        source = "\n".join([
            (ROOT / "models" / "bosta_return_case.py").read_text(),
            (ROOT / "services" / "bosta_return_service.py").read_text(),
            (ROOT / "views" / "bosta_return_views.xml").read_text(),
        ])
        for forbidden in ("sale.order", "account.move", "ir.cron"):
            self.assertNotIn(forbidden, source)

    def test_05_return_audit_does_not_add_receiver_pii_fields(self):
        source = (ROOT / "models" / "bosta_return_case.py").read_text()
        for forbidden in ("receiver_name", "receiver_phone", "dropoff_first_line", "dropoff_address"):
            self.assertNotIn(forbidden, source)

    def test_06_no_business_reference_authoritative_lookup_in_return_service(self):
        source = (ROOT / "services" / "bosta_return_service.py").read_text()
        self.assertNotIn("business_reference", source)
        self.assertNotIn("unique_business_reference", source)

    def test_07_return_case_has_database_uniqueness(self):
        source = (ROOT / "models" / "bosta_return_case.py").read_text()
        self.assertIn("unique(company_id, return_delivery_id)", source)
        self.assertIn("unique(company_id, return_case_id)", source)

    def test_08_historical_snapshot_fields_are_present(self):
        source = (ROOT / "models" / "bosta_return_case.py").read_text()
        for required in (
            "original_inventory_effect_line_id",
            "source_location_id",
            "destination_location_id",
            "product_id",
            "quantity",
            "picking_id",
            "applied_at",
        ):
            self.assertIn(required, source)

    def test_09_customer_return_tester_is_not_prepared(self):
        source = (ROOT / "services" / "bosta_return_service.py").read_text()
        customer_section = source.split("def _prepare_customer_lines", 1)[1].split("def _restoration_effect", 1)[0]
        self.assertIn('"role": "main"', customer_section)
        self.assertNotIn('"role": "tester"', customer_section)

    def test_10_rto_uses_original_effect_snapshot_locations(self):
        source = (ROOT / "services" / "bosta_return_service.py").read_text()
        rto_section = source.split("def _prepare_rto_lines", 1)[1].split("def _prepare_customer_lines", 1)[0]
        self.assertIn('"source": effect.transit_location_id', rto_section)
        self.assertIn('"destination": effect.source_location_id', rto_section)
