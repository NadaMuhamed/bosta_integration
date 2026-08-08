from pathlib import Path

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase


class TestBostaPhase5Baseline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["bosta.integration.config"]
        cls.Delivery = cls.env["bosta.delivery"]

    def test_01_manifest_version_dependencies_and_installability(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.10.0.0")
        self.assertEqual(manifest.get("depends"), ["base", "stock"])
        self.assertTrue(manifest.get("installable"))
        for forbidden in ("sale", "account", "website"):
            self.assertNotIn(forbidden, manifest.get("depends", []))

    def test_02_no_cron_or_background_sync_is_added(self):
        module_path = Path(get_module_path("bosta_integration"))
        xml = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in sorted(module_path.rglob("*.xml"))
        )
        self.assertNotIn('model="ir.cron"', xml)
        self.assertNotIn("interval_number", xml)
        self.assertNotIn("nextcall", xml)

    def test_03_phase4_normalizer_and_extraction_remain_orm_free(self):
        module_path = Path(get_module_path("bosta_integration"))
        rendered = "\n".join(
            (module_path / "services" / filename).read_text(encoding="utf-8").lower()
            for filename in ("bosta_delivery_normalizer.py", "bosta_extraction_service.py")
        )
        for forbidden in ('env[', '.create(', '.write(', 'requests.', 'authorization', 'api_key'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_04_persistence_service_has_no_direct_http_or_normalization_mapping(self):
        module_path = Path(get_module_path("bosta_integration"))
        rendered = (module_path / "services" / "bosta_persistence_service.py").read_text(encoding="utf-8").lower()
        for forbidden in ("requests.", "transport.request", "authorization", "_normalize_state", "_normalize_pricing"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)
        self.assertIn("iter_normalized_search_deliveries", rendered)
        self.assertIn("get_normalized_delivery_details", rendered)

    def test_05_no_raw_payload_or_timeline_storage_fields(self):
        forbidden = {
            "raw_payload", "raw_json", "search_payload", "details_payload",
            "timeline_json", "raw_timeline",
        }
        self.assertFalse(forbidden.intersection(self.Delivery._fields))

    def test_06_sync_audit_fields_exist_and_are_readonly(self):
        fields = (
            "last_sync_started_at", "last_sync_completed_at", "last_sync_status",
            "last_sync_seen_count", "last_sync_created_count", "last_sync_updated_count",
            "last_sync_unchanged_count", "last_sync_conflict_count", "last_sync_error_count",
            "last_sync_error",
        )
        for field_name in fields:
            with self.subTest(field=field_name):
                self.assertIn(field_name, self.Config._fields)
                self.assertTrue(self.Config._fields[field_name].readonly)

    def test_07_phase5_runtime_does_not_create_business_documents_or_touch_stock(self):
        module_path = Path(get_module_path("bosta_integration"))
        rendered = "\n".join(
            (module_path / relative).read_text(encoding="utf-8").lower()
            for relative in (
                "services/bosta_persistence_service.py",
            )
        )
        forbidden = (
            'env["res.partner"]', "env['res.partner']",
            'env["sale.order"]', "env['sale.order']",
            'env["product.product"]', "env['product.product']",
            'env["stock.move"]', "env['stock.move']",
            'env["account.move"]', "env['account.move']",
            "stock.picking", "quantity deduction", "inventory adjustment",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, rendered)

    def test_08_phase5_does_not_auto_link_returns_or_interpret_lifecycle(self):
        module_path = Path(get_module_path("bosta_integration"))
        persistence = (module_path / "services" / "bosta_persistence_service.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("original_delivery_id", persistence)
        for forbidden in ("physical_return_complete", "stock_restored", "net_profit", "settlement"):
            self.assertNotIn(forbidden, persistence)

    def test_09_manual_sync_is_explicit_manager_only_view_action(self):
        module_path = Path(get_module_path("bosta_integration"))
        view = (module_path / "views" / "bosta_config_views.xml").read_text(encoding="utf-8")
        self.assertIn('name="action_sync_bosta_deliveries"', view)
        self.assertIn('string="Sync Bosta Deliveries"', view)
        self.assertIn('groups="bosta_integration.group_bosta_integration_manager"', view)
