from pathlib import Path
from unittest.mock import patch

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase

from ..services.bosta_extraction_service import BostaExtractionService


class FakeClient:
    def __init__(self, search=None, details=None):
        self.search = list(search or [])
        self.details = details
        self.details_calls = []

    def iter_all_deliveries(self, **_kwargs):
        yield from self.search

    def get_delivery_details(self, tracking_number):
        self.details_calls.append(tracking_number)
        return self.details


class TestBostaPhase4Baseline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env["bosta.delivery"]
        cls.Item = cls.env["bosta.delivery.item"]

    def _counts(self):
        result = {
            "bosta.delivery": self.Delivery.sudo().search_count([]),
            "bosta.delivery.item": self.Item.sudo().search_count([]),
        }
        for model_name in ("res.partner", "sale.order", "stock.move", "account.move"):
            if self.env.registry.get(model_name) is not None:
                result[model_name] = self.env[model_name].sudo().search_count([])
        return result

    def test_70_search_extraction_creates_zero_delivery_records(self):
        before = self._counts()
        client = FakeClient(search=[{"_id": "A", "trackingNumber": "T1", "productInfo": [{"title": "Item"}]}])
        list(BostaExtractionService(client).iter_normalized_search_deliveries())
        self.assertEqual(self._counts(), before)

    def test_71_details_extraction_creates_zero_delivery_records(self):
        before = self._counts()
        client = FakeClient(details={"_id": "A", "trackingNumber": "T1", "productInfo": [{"title": "Item"}]})
        BostaExtractionService(client).get_normalized_delivery_details("T1")
        self.assertEqual(self._counts(), before)

    def test_72_search_extraction_creates_zero_delivery_item_records(self):
        before = self.Item.sudo().search_count([])
        client = FakeClient(search=[{"_id": "A", "trackingNumber": "T1", "productInfo": [{"title": "Item"}]}])
        list(BostaExtractionService(client).iter_normalized_search_deliveries())
        self.assertEqual(self.Item.sudo().search_count([]), before)

    def test_73_no_partner_is_created(self):
        before = self.env["res.partner"].sudo().search_count([])
        client = FakeClient(search=[{"_id": "A", "trackingNumber": "T1", "receiver": {"fullName": "Synthetic"}}])
        list(BostaExtractionService(client).iter_normalized_search_deliveries())
        self.assertEqual(self.env["res.partner"].sudo().search_count([]), before)

    def test_74_no_sale_order_is_created_when_model_available(self):
        model = self.env.registry.get("sale.order")
        if model is None:
            return
        before = self.env["sale.order"].sudo().search_count([])
        list(BostaExtractionService(FakeClient(search=[{"_id": "A", "trackingNumber": "T1"}])).iter_normalized_search_deliveries())
        self.assertEqual(self.env["sale.order"].sudo().search_count([]), before)

    def test_75_no_stock_move_is_created_when_model_available(self):
        model = self.env.registry.get("stock.move")
        if model is None:
            return
        before = self.env["stock.move"].sudo().search_count([])
        BostaExtractionService(FakeClient(details={"_id": "A", "trackingNumber": "T1"})).get_normalized_delivery_details("T1")
        self.assertEqual(self.env["stock.move"].sudo().search_count([]), before)

    def test_phase4_manifest_and_boundary(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.10.0.0")
        self.assertEqual(manifest.get("depends"), ["base", "stock"])
        module_path = Path(get_module_path("bosta_integration"))
        xml = "\n".join(path.read_text(encoding="utf-8").lower() for path in sorted((module_path / "views").glob("*.xml")))
        self.assertNotIn('model="ir.cron"', xml)

    def test_phase4_no_raw_payload_fields_and_no_orm_in_services(self):
        forbidden_fields = {"raw_payload", "raw_json", "search_payload", "details_payload", "timeline_json"}
        self.assertFalse(forbidden_fields.intersection(self.Delivery._fields))
        module_path = Path(get_module_path("bosta_integration"))
        rendered = "\n".join(
            (module_path / "services" / filename).read_text(encoding="utf-8").lower()
            for filename in ("bosta_delivery_normalizer.py", "bosta_extraction_service.py")
        )
        for forbidden in ('env[', '.create(', '.write(', 'requests.', 'authorization', 'bosta_api_key'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_phase4_normalizer_and_extraction_do_zero_http_beyond_injected_client(self):
        with patch(
            "odoo.addons.bosta_integration.services.bosta_api_client.requests.request",
            side_effect=AssertionError("Phase 4 pure normalization must not perform direct HTTP"),
        ):
            result = list(BostaExtractionService(FakeClient(search=[{"_id": "A", "trackingNumber": "T1"}])).iter_normalized_search_deliveries())
        self.assertEqual(result[0]["values"]["bosta_delivery_id"], "A")
