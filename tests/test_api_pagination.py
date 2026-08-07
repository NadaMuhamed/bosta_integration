from unittest import TestCase
from unittest.mock import patch

from ..services.bosta_api_client import BostaApiClient
from ..services.exceptions import BostaApiPaginationError
from ._helpers import FakeResponse, QueueTransport


ENV = {"BOSTA_API_KEY": "test-bosta-key-do-not-use"}


def delivery(index):
    return {"_id": f"id-{index}", "trackingNumber": f"track-{index}"}


def payload(deliveries, **data_extra):
    data = {"deliveries": deliveries}
    data.update(data_extra)
    return {"success": True, "data": data}


class TestBostaApiPagination(TestCase):
    def setUp(self):
        super().setUp()
        self._network_guard = patch(
            "requests.request",
            side_effect=AssertionError("Live Bosta/network requests are forbidden in automated tests"),
        )
        self._network_guard.start()
        self.addCleanup(self._network_guard.stop)

    def client(self, page_payloads, **kwargs):
        transport = QueueTransport([FakeResponse(payload=item) for item in page_payloads])
        client = BostaApiClient(transport=transport, sleep=lambda _value: None, environ=ENV, **kwargs)
        return client, transport

    def test_short_pages_stop_after_first_page(self):
        for count in (0, 1, 1499):
            items = [delivery(index) for index in range(count)]
            client, transport = self.client([payload(items)])
            self.assertEqual(len(client.get_all_deliveries()), count)
            self.assertEqual(len(transport.calls), 1)

    def test_exactly_full_page_requests_page_two(self):
        first = [delivery(index) for index in range(1500)]
        client, transport = self.client([payload(first), payload([])])
        self.assertEqual(len(client.get_all_deliveries()), 1500)
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2])

    def test_1500_plus_1_returns_1501_unique(self):
        first = [delivery(index) for index in range(1500)]
        second = [delivery(1500)]
        client, transport = self.client([payload(first), payload(second)])
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 1501)
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2])

    def test_three_pages_increment_correctly(self):
        first = [delivery(index) for index in range(1500)]
        second = [delivery(index) for index in range(1500, 3000)]
        third = [delivery(3000)]
        client, transport = self.client([payload(first), payload(second), payload(third)])
        self.assertEqual(len(client.get_all_deliveries()), 3001)
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2, 3])

    def test_metadata_can_stop_a_full_page(self):
        first = [delivery(index) for index in range(1500)]
        client, transport = self.client([payload(first, pagination={"currentPage": 1, "totalPages": 1})])
        self.assertEqual(len(client.get_all_deliveries()), 1500)
        self.assertEqual(len(transport.calls), 1)

    def test_duplicate_id_across_pages_is_deduplicated(self):
        first = [delivery(index) for index in range(1500)]
        duplicate = dict(first[-1])
        second = [duplicate, delivery(1500)]
        client, _ = self.client([payload(first), payload(second)])
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 1501)
        self.assertEqual(result[-1]["_id"], "id-1500")

    def test_duplicate_tracking_number_without_id_is_deduplicated(self):
        first = [{"trackingNumber": f"t-{index}"} for index in range(1500)]
        second = [{"trackingNumber": "t-1499"}, {"trackingNumber": "t-1500"}]
        client, _ = self.client([payload(first), payload(second)])
        self.assertEqual(len(client.get_all_deliveries()), 1501)

    def test_same_tracking_number_with_different_ids_is_one_logical_delivery(self):
        first = [
            {"_id": "delivery-a", "trackingNumber": "1234567890"},
            {"_id": "other-a", "trackingNumber": "other-a"},
        ]
        second = [
            {"_id": "delivery-b", "trackingNumber": "1234567890"},
            {"_id": "new-b", "trackingNumber": "new-b"},
        ]
        client, _ = self.client([payload(first), payload(second), payload([])], page_size=2)
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(item.get("trackingNumber") == "1234567890" for item in result), 1)

    def test_same_id_with_changed_tracking_payload_is_one_logical_delivery(self):
        first = [
            {"_id": "delivery-a", "trackingNumber": "tracking-old"},
            {"_id": "other-a", "trackingNumber": "other-a"},
        ]
        second = [
            {"_id": "delivery-a", "trackingNumber": "tracking-new"},
            {"_id": "new-b", "trackingNumber": "new-b"},
        ]
        client, _ = self.client([payload(first), payload(second), payload([])], page_size=2)
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(item.get("_id") == "delivery-a" for item in result), 1)

    def test_duplicate_identity_aliases_are_propagated(self):
        first = [
            {"_id": "A", "trackingNumber": "T1"},
            {"_id": "X", "trackingNumber": "TX"},
        ]
        second = [
            {"_id": "A", "trackingNumber": "T2"},
            {"_id": "Y", "trackingNumber": "TY"},
        ]
        third = [
            {"_id": "B", "trackingNumber": "T2"},
            {"_id": "Z", "trackingNumber": "TZ"},
        ]
        client, transport = self.client(
            [payload(first), payload(second), payload(third), payload([])],
            page_size=2,
        )

        result = client.get_all_deliveries()

        self.assertEqual([item["_id"] for item in result], ["A", "X", "Y", "Z"])
        self.assertNotIn("B", [item["_id"] for item in result])
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2, 3, 4])

    def test_repeated_identical_full_page_raises(self):
        first = [delivery(index) for index in range(1500)]
        client, transport = self.client([payload(first), payload(first)])
        with self.assertRaises(BostaApiPaginationError):
            client.get_all_deliveries()
        self.assertEqual(len(transport.calls), 2)

    def test_same_full_page_in_different_order_raises(self):
        first = [delivery(index) for index in range(4)]
        client, transport = self.client([payload(first), payload(list(reversed(first)))], page_size=4)
        with self.assertRaises(BostaApiPaginationError):
            client.get_all_deliveries()
        self.assertEqual(len(transport.calls), 2)

    def test_no_progress_with_changed_duplicate_payloads_raises(self):
        first = [
            {"_id": "a", "trackingNumber": "ta"},
            {"_id": "b", "trackingNumber": "tb"},
        ]
        second = [
            {"_id": "changed-a", "trackingNumber": "ta", "status": "updated"},
            {"_id": "changed-b", "trackingNumber": "tb", "status": "updated"},
        ]
        client, transport = self.client([payload(first), payload(second)], page_size=2)
        with self.assertRaises(BostaApiPaginationError):
            client.get_all_deliveries()
        self.assertEqual(len(transport.calls), 2)

    def test_max_pages_protection_raises_instead_of_false_completion(self):
        first = [delivery(index) for index in range(1500)]
        client, transport = self.client([payload(first)], max_pages=1)
        with self.assertRaises(BostaApiPaginationError):
            client.get_all_deliveries()
        self.assertEqual(len(transport.calls), 1)

    def test_configured_page_size_is_respected(self):
        first = [delivery(index) for index in range(3)]
        second = [delivery(3)]
        client, transport = self.client([payload(first), payload(second)], page_size=3)
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 4)
        self.assertEqual([call[2]["json"]["limit"] for call in transport.calls], [3, 3])

    def test_full_page_ignores_unreliable_zero_count(self):
        first = [delivery(index) for index in range(200)]
        second = [delivery(200)]
        client, transport = self.client(
            [
                payload(first, count=0, pagination={"currentPage": 1, "totalPages": 1, "hasNext": False}),
                payload(second, count=0),
            ],
            page_size=200,
        )
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 201)
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2])

    def test_full_1500_page_with_unreliable_count_still_continues(self):
        first = [delivery(index) for index in range(1500)]
        second = [delivery(1500)]
        client, transport = self.client(
            [
                payload(first, count=0, pagination={"currentPage": 1, "totalPages": 1}),
                payload(second, count=0),
            ]
        )
        result = client.get_all_deliveries()
        self.assertEqual(len(result), 1501)
        self.assertEqual([call[2]["json"]["page"] for call in transport.calls], [1, 2])
