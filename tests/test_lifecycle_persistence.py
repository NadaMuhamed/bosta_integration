from datetime import datetime, timedelta

from odoo.tests import TransactionCase

from ..services.bosta_persistence_service import BostaPersistenceService


class FakeExtractionService:
    def __init__(self, search=None, details=None):
        self.search = list(search or [])
        self.details = details
        self.search_calls = []
        self.details_calls = []

    def iter_normalized_search_deliveries(self, **kwargs):
        self.search_calls.append(kwargs)
        yield from self.search

    def get_normalized_delivery_details(self, tracking_number):
        self.details_calls.append(tracking_number)
        return self.details


class TestBostaLifecyclePersistence(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Bosta Phase 6 Persistence"})
        cls.Delivery = cls.env["bosta.delivery"].sudo()
        cls.service = BostaPersistenceService(cls.env)
        cls.counter = 0

    @classmethod
    def _normalized(cls, *, state="Processing", flow_code=10, flow_value="Send",
                    updated_at=None, timeline=None, source_kind="search", **extra):
        cls.counter += 1
        values = {
            "bosta_delivery_id": f"phase6-persist-id-{cls.counter}",
            "tracking_number": f"phase6-persist-track-{cls.counter}",
            "delivery_type_code": flow_code,
            "delivery_type_value": flow_value,
            "state_value": state,
        }
        if updated_at is not None:
            values["bosta_updated_at"] = updated_at
        values.update(extra)
        return {
            "values": values,
            "items": None,
            "timeline": timeline,
            "source_kind": source_kind,
        }

    def test_01_create_persists_derived_lifecycle(self):
        normalized = self._normalized(state="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        self.assertEqual(record.lifecycle_stage, "delivered_to_customer")
        self.assertEqual(record.return_scenario, "none")
        self.assertEqual(record.lifecycle_rule_code, "forward_delivered")
        self.assertFalse(record.lifecycle_ambiguous)

    def test_02_api_supplied_lifecycle_values_are_not_authoritative(self):
        normalized = self._normalized(state="Processing")
        normalized["values"].update({
            "lifecycle_stage": "delivered_to_customer",
            "return_scenario": "lost",
            "lifecycle_rule_code": "user_supplied",
            "lifecycle_ambiguous": False,
        })
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        self.assertEqual(record.lifecycle_stage, "pre_pickup")
        self.assertEqual(record.lifecycle_rule_code, "forward_pre_pickup")

    def test_03_identical_lifecycle_second_sync_is_unchanged(self):
        normalized = self._normalized(state="Picked Up")
        first = self.service.upsert_normalized_delivery(normalized, self.company)
        second = self.service.upsert_normalized_delivery(normalized, self.company)
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")

    def test_04_identical_lifecycle_second_sync_preserves_write_date(self):
        normalized = self._normalized(state="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        record.flush_recordset(["write_date"])
        before = record.write_date
        self.service.upsert_normalized_delivery(normalized, self.company)
        record.invalidate_recordset(["write_date"])
        self.assertEqual(record.write_date, before)

    def test_05_identical_lifecycle_does_not_duplicate_delivery(self):
        normalized = self._normalized(state="Delivered")
        self.service.upsert_normalized_delivery(normalized, self.company)
        self.service.upsert_normalized_delivery(normalized, self.company)
        count = self.Delivery.search_count([
            ("company_id", "=", self.company.id),
            ("bosta_delivery_id", "=", normalized["values"]["bosta_delivery_id"]),
        ])
        self.assertEqual(count, 1)

    def test_06_stale_picked_up_cannot_regress_delivered_customer(self):
        newer = datetime(2026, 8, 8, 12, 0)
        normalized = self._normalized(state="Delivered", updated_at=newer)
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        stale = self._normalized(
            state="Picked Up",
            updated_at=newer - timedelta(hours=2),
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        result = self.service.upsert_normalized_delivery(stale, self.company)
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(record.lifecycle_stage, "delivered_to_customer")

    def test_07_stale_processing_cannot_regress_returned_to_origin(self):
        newer = datetime(2026, 8, 8, 12, 0)
        normalized = self._normalized(
            state="Delivered", flow_code=20, flow_value="Return to Origin", updated_at=newer
        )
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        stale = self._normalized(
            state="Processing", flow_code=20, flow_value="Return to Origin",
            updated_at=newer - timedelta(hours=1),
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(stale, self.company)
        self.assertEqual(record.lifecycle_stage, "returned_to_origin")

    def test_08_stale_customer_return_pickup_cannot_regress_completed(self):
        newer = datetime(2026, 8, 8, 12, 0)
        normalized = self._normalized(
            state="Delivered", flow_code=25, flow_value="Customer Return Pickup", updated_at=newer
        )
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        stale = self._normalized(
            state="Processing", flow_code=25, flow_value="Customer Return Pickup",
            updated_at=newer - timedelta(minutes=1),
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(stale, self.company)
        self.assertEqual(record.lifecycle_stage, "customer_return_completed")

    def test_09_same_timestamp_details_can_strengthen_lifecycle(self):
        timestamp = datetime(2026, 8, 8, 12, 0)
        search = self._normalized(state="Picked Up", updated_at=timestamp)
        record = self.service.upsert_normalized_delivery(search, self.company)["record"]
        details = self._normalized(
            state="Processing",
            updated_at=timestamp,
            timeline=[{"value": "returned_to_origin", "done": True, "timestamp": timestamp}],
            source_kind="details",
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        result = self.service.upsert_normalized_delivery(details, self.company)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(record.lifecycle_stage, "returned_to_origin")
        self.assertEqual(record.lifecycle_rule_code, "timeline_returned_to_origin")

    def test_10_same_timestamp_weaker_search_does_not_regress_terminal_lifecycle(self):
        timestamp = datetime(2026, 8, 8, 12, 0)
        details = self._normalized(
            state="Processing",
            updated_at=timestamp,
            timeline=[{"value": "returned_to_origin", "done": True}],
            source_kind="details",
        )
        record = self.service.upsert_normalized_delivery(details, self.company)["record"]
        search = self._normalized(
            state="Processing",
            updated_at=timestamp,
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(search, self.company)
        self.assertEqual(record.lifecycle_stage, "returned_to_origin")

    def test_11_search_persistence_does_not_call_details(self):
        normalized = self._normalized(state="Processing")
        extraction = FakeExtractionService(search=[normalized], details=normalized)
        summary = self.service.persist_search_deliveries(extraction, self.company)
        self.assertEqual(summary["seen"], 1)
        self.assertEqual(extraction.details_calls, [])
        self.assertEqual(len(extraction.search_calls), 1)

    def test_12_explicit_details_enrichment_uses_existing_details_path_once(self):
        search = self._normalized(state="Processing")
        record = self.service.upsert_normalized_delivery(search, self.company)["record"]
        details = self._normalized(
            state="Delivered",
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            source_kind="details",
        )
        extraction = FakeExtractionService(details=details)
        result = self.service.enrich_delivery_from_details(extraction, record)
        self.assertEqual(extraction.details_calls, [record.tracking_number])
        self.assertEqual(result["record"].lifecycle_stage, "delivered_to_customer")

    def test_13_cod_changes_do_not_drive_lifecycle(self):
        normalized = self._normalized(state="Processing", cod_amount=0.0)
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        self.assertEqual(record.lifecycle_stage, "pre_pickup")

    def test_14_no_timeline_is_persisted(self):
        normalized = self._normalized(
            state="Processing",
            timeline=[{"value": "out_for_return", "done": True}],
            source_kind="details",
        )
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        self.assertNotIn("timeline", record._fields)
        self.assertEqual(record.lifecycle_stage, "returning_to_origin")

    def test_15_sparse_patch_preserves_nonterminal_lifecycle_context(self):
        normalized = self._normalized(state="Picked Up")
        record = self.service.upsert_normalized_delivery(normalized, self.company)["record"]
        sparse = self._normalized(
            state="Picked Up",
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        sparse["values"].pop("state_value")
        sparse["values"].pop("delivery_type_code")
        sparse["values"].pop("delivery_type_value")
        sparse["values"]["shipping_fee"] = 83.0
        self.service.upsert_normalized_delivery(sparse, self.company)
        self.assertEqual(record.lifecycle_stage, "with_bosta")
        self.assertEqual(record.shipping_fee, 83.0)

