from unittest import TestCase

from ..services.bosta_extraction_service import BostaExtractionService


class FakeBostaClient:
    def __init__(self, search_payloads=None, details_payload=None):
        self.search_payloads = list(search_payloads or [])
        self.details_payload = details_payload
        self.search_iter_calls = []
        self.details_calls = []

    def iter_all_deliveries(self, **kwargs):
        self.search_iter_calls.append(kwargs)
        for payload in self.search_payloads:
            yield payload

    def get_delivery_details(self, tracking_number):
        self.details_calls.append(tracking_number)
        if self.details_payload is None:
            raise AssertionError("Unexpected Details request")
        return self.details_payload


class TestBostaApiExtraction(TestCase):
    def test_66_search_extraction_uses_client_paginator(self):
        client = FakeBostaClient([{"_id": "A", "trackingNumber": "T1"}])
        service = BostaExtractionService(client)
        result = list(service.iter_normalized_search_deliveries(page_size=200, max_pages=4))
        self.assertEqual(client.search_iter_calls, [{"page_size": 200, "max_pages": 4}])
        self.assertEqual(result[0]["values"]["bosta_delivery_id"], "A")

    def test_67_normalized_records_are_streamed_one_by_one(self):
        yielded = []

        class StreamingClient(FakeBostaClient):
            def iter_all_deliveries(self, **kwargs):
                self.search_iter_calls.append(kwargs)
                yielded.append("first-requested")
                yield {"_id": "A", "trackingNumber": "T1"}
                yielded.append("second-requested")
                yield {"_id": "B", "trackingNumber": "T2"}

        client = StreamingClient()
        iterator = BostaExtractionService(client).iter_normalized_search_deliveries()
        self.assertEqual(yielded, [])
        first = next(iterator)
        self.assertEqual(first["values"]["bosta_delivery_id"], "A")
        self.assertEqual(yielded, ["first-requested"])
        second = next(iterator)
        self.assertEqual(second["values"]["bosta_delivery_id"], "B")

    def test_68_search_extraction_never_calls_details_implicitly(self):
        client = FakeBostaClient([
            {"_id": "A", "trackingNumber": "T1"},
            {"_id": "B", "trackingNumber": "T2"},
            {"_id": "C", "trackingNumber": "T3"},
        ])
        result = list(BostaExtractionService(client).iter_normalized_search_deliveries())
        self.assertEqual(len(result), 3)
        self.assertEqual(client.details_calls, [])

    def test_69_explicit_details_calls_details_exactly_once(self):
        client = FakeBostaClient(details_payload={"_id": "A", "trackingNumber": "T1", "shipmentFees": 7})
        result = BostaExtractionService(client).get_normalized_delivery_details("T1")
        self.assertEqual(client.details_calls, ["T1"])
        self.assertEqual(result["source_kind"], "details")
        self.assertEqual(result["values"]["shipment_fees"], 7.0)

    def test_search_extraction_does_not_materialize_input_list(self):
        client = FakeBostaClient([{"_id": "A", "trackingNumber": "T1"}])
        iterator = BostaExtractionService(client).iter_normalized_search_deliveries()
        self.assertFalse(isinstance(iterator, list))
        self.assertEqual(next(iterator)["source_kind"], "search")

    def test_details_normalization_is_explicit_only(self):
        client = FakeBostaClient(
            search_payloads=[{"_id": "A", "trackingNumber": "T1"}],
            details_payload={"_id": "A", "trackingNumber": "T1"},
        )
        service = BostaExtractionService(client)
        list(service.iter_normalized_search_deliveries())
        self.assertEqual(client.details_calls, [])
        service.get_normalized_delivery_details("T1")
        self.assertEqual(client.details_calls, ["T1"])
