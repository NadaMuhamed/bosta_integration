from datetime import datetime, timedelta

from odoo.tests import TransactionCase

from ..services.bosta_persistence_service import BostaPersistenceService
from ..services.exceptions import BostaPersistenceIdentityConflict


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


class TestBostaPersistenceService(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Persistence A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Persistence B"})
        cls.Delivery = cls.env["bosta.delivery"].sudo()
        cls.Item = cls.env["bosta.delivery.item"].sudo()
        cls.service = BostaPersistenceService(cls.env)
        cls.counter = 0

    @classmethod
    def _normalized(cls, **values):
        cls.counter += 1
        base = {
            "bosta_delivery_id": f"phase5-id-{cls.counter}",
            "tracking_number": f"phase5-track-{cls.counter}",
        }
        base.update(values)
        return {
            "values": base,
            "items": None,
            "timeline": None,
            "source_kind": "search",
        }

    @staticmethod
    def _item(pid="item-1", product="product-1", sequence=10, quantity=1, **extra):
        values = {
            "sequence": sequence,
            "bosta_product_info_id": pid,
            "external_product_id": product,
            "title": "Synthetic Item",
            "quantity": quantity,
            "product_type": "forward",
            "options_string": "Synthetic Option",
        }
        values.update(extra)
        return values

    def test_01_new_normalized_delivery_creates_one_record(self):
        normalized = self._normalized(state_value="Processing")
        result = self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["record"].company_id, self.company_a)
        self.assertEqual(result["record"].state_value, "Processing")

    def test_02_company_comes_from_sync_context(self):
        normalized = self._normalized()
        normalized["values"]["company_id"] = self.company_b.id
        result = self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(result["record"].company_id, self.company_a)

    def test_03_system_fields_from_normalized_values_are_ignored(self):
        normalized = self._normalized()
        normalized["values"].update({"id": 999999, "create_uid": 999999, "write_uid": 999999})
        result = self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertNotEqual(result["record"].id, 999999)

    def test_04_identical_second_sync_is_unchanged_and_not_duplicated(self):
        normalized = self._normalized(state_value="Delivered")
        first = self.service.upsert_normalized_delivery(normalized, self.company_a)
        second = self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "unchanged")
        self.assertEqual(
            self.Delivery.search_count([
                ("company_id", "=", self.company_a.id),
                ("bosta_delivery_id", "=", normalized["values"]["bosta_delivery_id"]),
            ]),
            1,
        )

    def test_05_identical_second_sync_preserves_parent_write_date(self):
        normalized = self._normalized(state_value="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        record.flush_recordset(["write_date"])
        before = record.write_date
        self.service.upsert_normalized_delivery(normalized, self.company_a)
        record.invalidate_recordset(["write_date"])
        self.assertEqual(record.write_date, before)

    def test_06_supplied_state_updates(self):
        normalized = self._normalized(state_value="Processing")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        changed = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            state_value="Delivered",
        )
        result = self.service.upsert_normalized_delivery(changed, self.company_a)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(record.state_value, "Delivered")

    def test_07_missing_state_preserves_existing(self):
        normalized = self._normalized(state_value="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        sparse = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(sparse, self.company_a)
        self.assertEqual(record.state_value, "Delivered")

    def test_08_explicit_false_or_null_clears_optional_state_value(self):
        normalized = self._normalized(state_value="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        clear = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            state_value=False,
        )
        self.service.upsert_normalized_delivery(clear, self.company_a)
        self.assertFalse(record.state_value)

    def test_09_explicit_cod_zero_overwrites_nonzero(self):
        normalized = self._normalized(cod_amount=500.0)
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        zero = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            cod_amount=0.0,
        )
        self.service.upsert_normalized_delivery(zero, self.company_a)
        self.assertEqual(record.cod_amount, 0.0)

    def test_10_missing_cod_preserves_previous_cod(self):
        normalized = self._normalized(cod_amount=500.0)
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        sparse = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(sparse, self.company_a)
        self.assertEqual(record.cod_amount, 500.0)

    def test_11_sparse_search_does_not_erase_details_pricing(self):
        normalized = self._normalized(shipping_fee=83.0, opening_package_fee=7.0, price_after_vat=97.5)
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        sparse = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.upsert_normalized_delivery(sparse, self.company_a)
        self.assertEqual(record.shipping_fee, 83.0)
        self.assertEqual(record.opening_package_fee, 7.0)
        self.assertEqual(record.price_after_vat, 97.5)

    def test_12_explicit_price_zero_is_persisted(self):
        normalized = self._normalized(price_after_vat=14.0)
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        zero = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            price_after_vat=0.0,
        )
        self.service.upsert_normalized_delivery(zero, self.company_a)
        self.assertEqual(record.price_after_vat, 0.0)

    def test_13_unknown_source_state_type_are_persisted(self):
        normalized = self._normalized(
            creation_source="FUTURE_SOURCE",
            state_value="Future State",
            delivery_type_code=999,
            delivery_type_value="Future Type",
        )
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        self.assertEqual(record.creation_source, "FUTURE_SOURCE")
        self.assertEqual(record.state_value, "Future State")
        self.assertEqual(record.delivery_type_value, "Future Type")
        self.assertEqual(record.flow_type, "other")

    def test_14_identity_both_fields_match_same_record(self):
        normalized = self._normalized(state_value="Processing")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        again = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            state_value="Delivered",
        )
        result = self.service.upsert_normalized_delivery(again, self.company_a)
        self.assertEqual(result["record"], record)
        self.assertEqual(record.state_value, "Delivered")

    def test_15_id_match_allows_free_tracking_alias_evolution(self):
        normalized = self._normalized()
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        evolved = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number="free-evolved-tracking",
        )
        result = self.service.upsert_normalized_delivery(evolved, self.company_a)
        self.assertEqual(result["record"], record)
        self.assertEqual(record.tracking_number, "free-evolved-tracking")

    def test_16_tracking_match_allows_free_delivery_id_alias_evolution(self):
        normalized = self._normalized()
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        evolved = self._normalized(
            bosta_delivery_id="free-evolved-id",
            tracking_number=record.tracking_number,
        )
        result = self.service.upsert_normalized_delivery(evolved, self.company_a)
        self.assertEqual(result["record"], record)
        self.assertEqual(record.bosta_delivery_id, "free-evolved-id")

    def test_17_split_brain_identity_conflict_changes_neither_record(self):
        first = self.service.upsert_normalized_delivery(self._normalized(), self.company_a)["record"]
        second = self.service.upsert_normalized_delivery(self._normalized(), self.company_a)["record"]
        first_before = (first.bosta_delivery_id, first.tracking_number, first.state_value)
        second_before = (second.bosta_delivery_id, second.tracking_number, second.state_value)
        conflict = self._normalized(
            bosta_delivery_id=first.bosta_delivery_id,
            tracking_number=second.tracking_number,
            state_value="Must Not Apply",
        )
        with self.assertRaises(BostaPersistenceIdentityConflict):
            self.service.upsert_normalized_delivery(conflict, self.company_a)
        self.assertEqual((first.bosta_delivery_id, first.tracking_number, first.state_value), first_before)
        self.assertEqual((second.bosta_delivery_id, second.tracking_number, second.state_value), second_before)

    def test_18_duplicate_business_references_still_allowed(self):
        first = self._normalized(business_reference="shared", unique_business_reference="shared-unique")
        second = self._normalized(business_reference="shared", unique_business_reference="shared-unique")
        one = self.service.upsert_normalized_delivery(first, self.company_a)["record"]
        two = self.service.upsert_normalized_delivery(second, self.company_a)["record"]
        self.assertNotEqual(one, two)
        self.assertEqual(one.business_reference, two.business_reference)
        self.assertEqual(one.unique_business_reference, two.unique_business_reference)

    def test_19_same_external_ids_are_company_isolated(self):
        normalized = self._normalized()
        one = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        two = self.service.upsert_normalized_delivery(normalized, self.company_b)["record"]
        self.assertNotEqual(one, two)
        self.assertEqual(one.bosta_delivery_id, two.bosta_delivery_id)
        self.assertNotEqual(one.company_id, two.company_id)

    def test_20_items_none_preserves_existing_items(self):
        normalized = self._normalized()
        normalized["items"] = [self._item()]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        sparse = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        sparse["items"] = None
        self.service.upsert_normalized_delivery(sparse, self.company_a)
        self.assertEqual(len(record.item_ids), 1)

    def test_21_items_empty_list_deletes_existing_items(self):
        normalized = self._normalized()
        normalized["items"] = [self._item()]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        empty = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        empty["items"] = []
        result = self.service.upsert_normalized_delivery(empty, self.company_a)
        self.assertEqual(result["action"], "updated")
        self.assertFalse(record.item_ids)

    def test_22_explicit_one_and_two_item_lists_create_exact_counts(self):
        one_payload = self._normalized()
        one_payload["items"] = [self._item(pid="one")]
        one = self.service.upsert_normalized_delivery(one_payload, self.company_a)["record"]
        self.assertEqual(len(one.item_ids), 1)

        two_payload = self._normalized()
        two_payload["items"] = [
            self._item(pid="a", sequence=10),
            self._item(pid="b", product="product-2", sequence=20),
        ]
        two = self.service.upsert_normalized_delivery(two_payload, self.company_a)["record"]
        self.assertEqual(len(two.item_ids), 2)

    def test_23_identical_item_resync_creates_no_duplicates(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="a"), self._item(pid="b", product="p2", sequence=20)]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        result = self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(len(record.item_ids), 2)

    def test_24_changed_item_quantity_updates_correct_item_and_zero_is_preserved(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="a", quantity=2), self._item(pid="b", product="p2", sequence=20)]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        changed = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        changed["items"] = [self._item(pid="a", quantity=0), self._item(pid="b", product="p2", sequence=20)]
        self.service.upsert_normalized_delivery(changed, self.company_a)
        item_a = record.item_ids.filtered(lambda item: item.bosta_product_info_id == "a")
        self.assertEqual(item_a.quantity, 0)
        self.assertEqual(len(record.item_ids), 2)

    def test_25_removed_explicit_item_is_deleted(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="a"), self._item(pid="b", product="p2", sequence=20)]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        reduced = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        reduced["items"] = [self._item(pid="a")]
        self.service.upsert_normalized_delivery(reduced, self.company_a)
        self.assertEqual(record.item_ids.mapped("bosta_product_info_id"), ["a"])

    def test_26_bosta_product_info_id_is_preferred_over_sequence(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="stable", sequence=10)]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        item_id = record.item_ids.id
        changed = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        changed["items"] = [self._item(pid="stable", sequence=30)]
        self.service.upsert_normalized_delivery(changed, self.company_a)
        self.assertEqual(record.item_ids.id, item_id)
        self.assertEqual(record.item_ids.sequence, 30)

    def test_27_fallback_item_identity_is_deterministic(self):
        normalized = self._normalized()
        item = self._item(pid=False, product="fallback-product", sequence=10)
        normalized["items"] = [item]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        first_item_id = record.item_ids.id
        self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(record.item_ids.id, first_item_id)
        self.assertEqual(len(record.item_ids), 1)

    def test_28_same_external_product_may_exist_in_multiple_deliveries(self):
        first = self._normalized()
        second = self._normalized()
        first["items"] = [self._item(pid=False, product="shared-product")]
        second["items"] = [self._item(pid=False, product="shared-product")]
        one = self.service.upsert_normalized_delivery(first, self.company_a)["record"]
        two = self.service.upsert_normalized_delivery(second, self.company_a)["record"]
        self.assertEqual(one.item_ids.external_product_id, "shared-product")
        self.assertEqual(two.item_ids.external_product_id, "shared-product")

    def test_29_newer_incoming_updated_at_updates_record(self):
        old = datetime(2026, 8, 6, 10, 0, 0)
        new = old + timedelta(hours=1)
        normalized = self._normalized(bosta_updated_at=old, state_value="Processing")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        newer = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            bosta_updated_at=new,
            state_value="Delivered",
        )
        self.service.upsert_normalized_delivery(newer, self.company_a)
        self.assertEqual(record.state_value, "Delivered")
        self.assertEqual(record.bosta_updated_at, new)

    def test_30_older_incoming_does_not_regress_state_or_items(self):
        new = datetime(2026, 8, 6, 11, 0, 0)
        old = new - timedelta(hours=1)
        normalized = self._normalized(bosta_updated_at=new, state_value="Delivered")
        normalized["items"] = [self._item(pid="new")]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        stale = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            bosta_updated_at=old,
            state_value="Processing",
        )
        stale["items"] = []
        result = self.service.upsert_normalized_delivery(stale, self.company_a)
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(record.state_value, "Delivered")
        self.assertEqual(record.item_ids.mapped("bosta_product_info_id"), ["new"])

    def test_31_same_timestamp_allows_details_enrichment(self):
        stamp = datetime(2026, 8, 6, 11, 0, 0)
        normalized = self._normalized(bosta_updated_at=stamp, state_value="Delivered")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        details = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            bosta_updated_at=stamp,
            shipping_fee=83.0,
        )
        result = self.service.upsert_normalized_delivery(details, self.company_a)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(record.shipping_fee, 83.0)

    def test_32_missing_incoming_updated_at_uses_partial_semantics(self):
        stamp = datetime(2026, 8, 6, 11, 0, 0)
        normalized = self._normalized(bosta_updated_at=stamp, shipping_fee=83.0, state_value="Processing")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        patch = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            state_value="Delivered",
        )
        self.service.upsert_normalized_delivery(patch, self.company_a)
        self.assertEqual(record.state_value, "Delivered")
        self.assertEqual(record.shipping_fee, 83.0)
        self.assertEqual(record.bosta_updated_at, stamp)

    def test_33_stale_identity_conflict_is_still_rejected(self):
        stamp = datetime(2026, 8, 6, 11, 0, 0)
        first = self.service.upsert_normalized_delivery(self._normalized(bosta_updated_at=stamp), self.company_a)["record"]
        second = self.service.upsert_normalized_delivery(self._normalized(bosta_updated_at=stamp), self.company_a)["record"]
        conflict = self._normalized(
            bosta_delivery_id=first.bosta_delivery_id,
            tracking_number=second.tracking_number,
            bosta_updated_at=stamp - timedelta(days=1),
        )
        with self.assertRaises(BostaPersistenceIdentityConflict):
            self.service.upsert_normalized_delivery(conflict, self.company_a)

    def test_34_search_persistence_uses_existing_extraction_iterator_and_streams(self):
        stream = [self._normalized(), self._normalized(), self._normalized()]
        extraction = FakeExtractionService(search=stream)
        summary = self.service.persist_search_deliveries(
            extraction,
            self.company_a,
            page_size=200,
            max_pages=5,
        )
        self.assertEqual(summary, {
            "seen": 3, "created": 3, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
        })
        self.assertEqual(extraction.search_calls, [{"page_size": 200, "max_pages": 5}])
        self.assertEqual(extraction.details_calls, [])

    def test_35_repeated_same_stream_remains_same_record_count(self):
        stream = [self._normalized(), self._normalized(), self._normalized()]
        extraction = FakeExtractionService(search=stream)
        first = self.service.persist_search_deliveries(extraction, self.company_a)
        second = self.service.persist_search_deliveries(FakeExtractionService(search=stream), self.company_a)
        self.assertEqual(first["created"], 3)
        self.assertEqual(second["unchanged"], 3)
        ids = [item["values"]["bosta_delivery_id"] for item in stream]
        self.assertEqual(self.Delivery.search_count([("company_id", "=", self.company_a.id), ("bosta_delivery_id", "in", ids)]), 3)

    def test_36_search_conflict_isolated_and_following_record_continues(self):
        first = self.service.upsert_normalized_delivery(self._normalized(), self.company_a)["record"]
        second = self.service.upsert_normalized_delivery(self._normalized(), self.company_a)["record"]
        conflict = self._normalized(bosta_delivery_id=first.bosta_delivery_id, tracking_number=second.tracking_number)
        valid = self._normalized()
        summary = self.service.persist_search_deliveries(FakeExtractionService(search=[conflict, valid]), self.company_a)
        self.assertEqual(summary["seen"], 2)
        self.assertEqual(summary["conflicts"], 1)
        self.assertEqual(summary["created"], 1)

    def test_37_explicit_details_enrichment_calls_details_once_and_updates_same_delivery(self):
        normalized = self._normalized(state_value="Processing")
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        details = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            state_value="Delivered",
            shipping_fee=83.0,
        )
        details["source_kind"] = "details"
        extraction = FakeExtractionService(details=details)
        result = self.service.enrich_delivery_from_details(extraction, record)
        self.assertEqual(extraction.details_calls, [record.tracking_number])
        self.assertEqual(result["record"], record)
        self.assertEqual(record.shipping_fee, 83.0)
        self.assertEqual(record.state_value, "Delivered")

    def test_38_details_pricing_survives_later_sparse_search(self):
        normalized = self._normalized()
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        details = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            shipping_fee=83.0,
            opening_package_fee=7.0,
        )
        self.service.enrich_delivery_from_details(FakeExtractionService(details=details), record)
        sparse = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
        )
        self.service.persist_search_deliveries(FakeExtractionService(search=[sparse]), self.company_a)
        self.assertEqual(record.shipping_fee, 83.0)
        self.assertEqual(record.opening_package_fee, 7.0)

    def test_39_timeline_is_not_persisted(self):
        normalized = self._normalized()
        normalized["timeline"] = [{"value": "Delivered", "done": True}]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        self.assertTrue(record.exists())
        for field in ("timeline_json", "raw_timeline", "raw_payload"):
            self.assertNotIn(field, record._fields)

    def test_40_original_delivery_is_never_auto_linked(self):
        original = self._normalized(business_reference="same-ref", delivery_type_code=10)
        returned = self._normalized(business_reference="same-ref", delivery_type_code=25)
        first = self.service.upsert_normalized_delivery(original, self.company_a)["record"]
        second = self.service.upsert_normalized_delivery(returned, self.company_a)["record"]
        self.assertFalse(first.original_delivery_id)
        self.assertFalse(second.original_delivery_id)

    def test_41_no_partner_product_order_stock_or_accounting_records_are_created(self):
        watched = {}
        for model_name in ("res.partner", "product.product", "sale.order", "stock.move", "account.move"):
            if self.env.registry.get(model_name) is not None:
                watched[model_name] = self.env[model_name].sudo().search_count([])
        normalized = self._normalized()
        normalized["items"] = [self._item()]
        self.service.upsert_normalized_delivery(normalized, self.company_a)
        for model_name, before in watched.items():
            with self.subTest(model=model_name):
                self.assertEqual(self.env[model_name].sudo().search_count([]), before)

    def test_42_unknown_future_creation_source_persists_safely(self):
        record = self.service.upsert_normalized_delivery(
            self._normalized(creation_source="FUTURE_SOURCE_ONLY"), self.company_a
        )["record"]
        self.assertEqual(record.creation_source, "FUTURE_SOURCE_ONLY")

    def test_43_unknown_future_state_persists_safely(self):
        record = self.service.upsert_normalized_delivery(
            self._normalized(state_code=999, state_value="Future Raw State"), self.company_a
        )["record"]
        self.assertEqual(record.state_code, 999)
        self.assertEqual(record.state_value, "Future Raw State")

    def test_44_unknown_future_delivery_type_persists_safely(self):
        record = self.service.upsert_normalized_delivery(
            self._normalized(delivery_type_code=999, delivery_type_value="Future Raw Type"), self.company_a
        )["record"]
        self.assertEqual(record.delivery_type_value, "Future Raw Type")
        self.assertEqual(record.flow_type, "other")

    def test_45_duplicate_business_reference_is_not_identity(self):
        one = self.service.upsert_normalized_delivery(
            self._normalized(business_reference="shared-reference"), self.company_a
        )["record"]
        two = self.service.upsert_normalized_delivery(
            self._normalized(business_reference="shared-reference"), self.company_a
        )["record"]
        self.assertNotEqual(one.id, two.id)

    def test_46_duplicate_unique_business_reference_is_not_identity(self):
        one = self.service.upsert_normalized_delivery(
            self._normalized(unique_business_reference="shared-unique-reference"), self.company_a
        )["record"]
        two = self.service.upsert_normalized_delivery(
            self._normalized(unique_business_reference="shared-unique-reference"), self.company_a
        )["record"]
        self.assertNotEqual(one.id, two.id)

    def test_47_explicit_one_item_list_creates_exactly_one_item(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="single-item")]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        self.assertEqual(len(record.item_ids), 1)

    def test_48_explicit_two_item_list_creates_exactly_two_items(self):
        normalized = self._normalized()
        normalized["items"] = [
            self._item(pid="first-item", sequence=10),
            self._item(pid="second-item", product="product-2", sequence=20),
        ]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        self.assertEqual(len(record.item_ids), 2)

    def test_49_explicit_item_quantity_zero_is_preserved(self):
        normalized = self._normalized()
        normalized["items"] = [self._item(pid="zero-item", quantity=0)]
        record = self.service.upsert_normalized_delivery(normalized, self.company_a)["record"]
        self.assertEqual(record.item_ids.quantity, 0)

    def test_50_search_persistence_never_calls_details(self):
        extraction = FakeExtractionService(search=[self._normalized(), self._normalized()])
        self.service.persist_search_deliveries(extraction, self.company_a)
        self.assertEqual(extraction.details_calls, [])

    def test_51_explicit_details_enrichment_calls_details_exactly_once(self):
        record = self.service.upsert_normalized_delivery(self._normalized(), self.company_a)["record"]
        details = self._normalized(
            bosta_delivery_id=record.bosta_delivery_id,
            tracking_number=record.tracking_number,
            shipment_fees=7.98,
        )
        extraction = FakeExtractionService(details=details)
        self.service.enrich_delivery_from_details(extraction, record)
        self.assertEqual(extraction.details_calls, [record.tracking_number])

    def test_52_no_product_product_is_created_when_model_available(self):
        if self.env.registry.get("product.product") is None:
            return
        before = self.env["product.product"].sudo().search_count([])
        normalized = self._normalized()
        normalized["items"] = [self._item()]
        self.service.upsert_normalized_delivery(normalized, self.company_a)
        self.assertEqual(self.env["product.product"].sudo().search_count([]), before)

    def test_53_no_res_partner_is_created(self):
        before = self.env["res.partner"].sudo().search_count([])
        self.service.upsert_normalized_delivery(
            self._normalized(receiver_name="Synthetic Receiver"), self.company_a
        )
        self.assertEqual(self.env["res.partner"].sudo().search_count([]), before)

    def test_54_no_sale_order_is_created_when_model_available(self):
        if self.env.registry.get("sale.order") is None:
            return
        before = self.env["sale.order"].sudo().search_count([])
        self.service.upsert_normalized_delivery(self._normalized(), self.company_a)
        self.assertEqual(self.env["sale.order"].sudo().search_count([]), before)

    def test_55_no_stock_move_is_created_when_model_available(self):
        if self.env.registry.get("stock.move") is None:
            return
        before = self.env["stock.move"].sudo().search_count([])
        self.service.upsert_normalized_delivery(self._normalized(), self.company_a)
        self.assertEqual(self.env["stock.move"].sudo().search_count([]), before)

    def test_56_no_account_move_is_created_when_model_available(self):
        if self.env.registry.get("account.move") is None:
            return
        before = self.env["account.move"].sudo().search_count([])
        self.service.upsert_normalized_delivery(self._normalized(), self.company_a)
        self.assertEqual(self.env["account.move"].sudo().search_count([]), before)
