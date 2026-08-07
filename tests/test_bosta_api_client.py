import requests
from unittest import TestCase
from unittest.mock import patch

from ..services.bosta_api_client import BostaApiClient
from ..services.exceptions import (
    BostaApiAuthenticationError,
    BostaApiConfigurationError,
    BostaApiConnectionError,
    BostaApiContractError,
    BostaApiNotFoundError,
    BostaApiPermissionError,
    BostaApiRateLimitError,
    BostaApiServerError,
    BostaApiTimeoutError,
)
from ._helpers import FakeResponse, QueueTransport


KEY = "test-bosta-key-do-not-use"
ENV = {"BOSTA_API_KEY": KEY}


def search_payload(deliveries, **extra):
    payload = {"success": True, "data": {"deliveries": deliveries}}
    payload.update(extra)
    return payload


class TestBostaApiClient(TestCase):
    def setUp(self):
        super().setUp()
        self._network_guard = patch(
            "requests.request",
            side_effect=AssertionError("Live Bosta/network requests are forbidden in automated tests"),
        )
        self._network_guard.start()
        self.addCleanup(self._network_guard.stop)

    def make_client(self, responses, **kwargs):
        transport = QueueTransport(responses)
        sleeps = []
        client = BostaApiClient(
            transport=transport,
            sleep=sleeps.append,
            environ=ENV,
            **kwargs,
        )
        return client, transport, sleeps

    def test_search_request_construction(self):
        client, transport, _ = self.make_client([FakeResponse(payload=search_payload([]))], timeout=17)
        deliveries, _payload = client.search_deliveries(page=3, limit=25)
        self.assertEqual(deliveries, [])
        method, url, kwargs = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://app.bosta.co/api/v2/deliveries/search")
        self.assertEqual(kwargs["json"], {"page": 3, "limit": 25})
        self.assertEqual(kwargs["headers"]["Authorization"], KEY)
        self.assertFalse(kwargs["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["timeout"], 17)

    def test_details_request_construction_and_text_encoding(self):
        client, transport, _ = self.make_client([FakeResponse(payload={"success": True, "data": {"_id": "d1"}})])
        data = client.get_delivery_details(" 12/34 ")
        self.assertEqual(data, {"_id": "d1"})
        method, url, kwargs = transport.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/api/v2/deliveries/business/12%2F34"))
        self.assertIsNone(kwargs["json"])

    def test_missing_or_whitespace_api_key_fails_before_network(self):
        for env in ({}, {"BOSTA_API_KEY": ""}, {"BOSTA_API_KEY": "   "}):
            transport = QueueTransport([])
            client = BostaApiClient(transport=transport, sleep=lambda _value: None, environ=env)
            with self.subTest(env=env), self.assertRaises(BostaApiConfigurationError):
                client.search_deliveries()
            self.assertEqual(transport.calls, [])

    def test_successful_contracts_accept_extra_fields(self):
        client, _, _ = self.make_client([
            FakeResponse(payload={"success": True, "message": "ok", "data": {"deliveries": [{"_id": "1"}], "extra": 1}, "extra": 2}),
            FakeResponse(payload={"success": True, "message": "ok", "data": {"trackingNumber": "123", "extra": 3}, "extra": 4}),
        ])
        deliveries, _ = client.search_deliveries()
        details = client.get_delivery_details("123")
        self.assertEqual(deliveries[0]["_id"], "1")
        self.assertEqual(details["trackingNumber"], "123")

    def test_invalid_json_is_safe_contract_error(self):
        client, _, _ = self.make_client([FakeResponse(json_error=ValueError("secret body"))])
        with self.assertRaises(BostaApiContractError) as caught:
            client.search_deliveries()
        self.assertNotIn("secret body", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    def test_search_contract_failures(self):
        invalid_payloads = [
            [],
            {"success": False, "data": {"deliveries": []}},
            {"success": True},
            {"success": True, "data": []},
            {"success": True, "data": {}},
            {"success": True, "data": {"deliveries": {}}},
        ]
        for payload in invalid_payloads:
            client, _, _ = self.make_client([FakeResponse(payload=payload)])
            with self.subTest(payload=payload), self.assertRaises(BostaApiContractError):
                client.search_deliveries()

    def test_search_requires_explicit_boolean_success_true(self):
        for success_marker in (None, 1, "true", "True"):
            payload = {"data": {"deliveries": []}}
            if success_marker is not None:
                payload["success"] = success_marker
            client, _, _ = self.make_client([FakeResponse(payload=payload)])
            with self.subTest(success=success_marker), self.assertRaises(BostaApiContractError):
                client.search_deliveries()

    def test_details_contract_failures_are_safe(self):
        payloads = [
            [],
            {"success": False, "data": {}},
            {"success": True},
            {"success": True, "data": []},
        ]
        for payload in payloads:
            client, _, _ = self.make_client([FakeResponse(payload=payload)])
            with self.subTest(payload=payload), self.assertRaises(BostaApiContractError) as caught:
                client.get_delivery_details("123")
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(repr(payload), str(caught.exception))

        client, _, _ = self.make_client([FakeResponse(json_error=ValueError("secret-response-body"))])
        with self.assertRaises(BostaApiContractError) as caught:
            client.get_delivery_details("123")
        self.assertNotIn("secret-response-body", str(caught.exception))

    def test_non_retry_http_mappings(self):
        cases = [
            (400, BostaApiContractError),
            (401, BostaApiAuthenticationError),
            (403, BostaApiPermissionError),
            (404, BostaApiContractError),
        ]
        for status, exception_type in cases:
            client, transport, sleeps = self.make_client([FakeResponse(status_code=status)])
            with self.subTest(status=status), self.assertRaises(exception_type):
                client.search_deliveries()
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(sleeps, [])

        client, transport, sleeps = self.make_client([FakeResponse(status_code=404)])
        with self.assertRaises(BostaApiNotFoundError):
            client.get_delivery_details("123")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sleeps, [])

    def test_retryable_http_statuses_are_bounded(self):
        mapping = {
            429: BostaApiRateLimitError,
            500: BostaApiServerError,
            502: BostaApiServerError,
            503: BostaApiServerError,
            504: BostaApiServerError,
        }
        for status, exception_type in mapping.items():
            client, transport, sleeps = self.make_client(
                [FakeResponse(status_code=status), FakeResponse(status_code=status), FakeResponse(status_code=status)],
                max_retries=2,
            )
            with self.subTest(status=status), self.assertRaises(exception_type):
                client.search_deliveries()
            self.assertEqual(len(transport.calls), 3)
            self.assertEqual(len(sleeps), 2)

    def test_temporary_http_failure_then_success(self):
        for status in (429, 500, 503):
            client, transport, sleeps = self.make_client([
                FakeResponse(status_code=status),
                FakeResponse(payload=search_payload([])),
            ])
            with self.subTest(status=status):
                deliveries, _ = client.search_deliveries()
            self.assertEqual(deliveries, [])
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(len(sleeps), 1)

    def test_connection_failure_then_success(self):
        client, transport, sleeps = self.make_client([
            requests.ConnectionError("secret connection error"),
            FakeResponse(payload=search_payload([])),
        ])
        deliveries, _ = client.search_deliveries()
        self.assertEqual(deliveries, [])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(sleeps), 1)

    def test_retry_after_edge_cases_are_bounded(self):
        cases = [
            ("-5", 0.25),
            ("not-a-number", 0.25),
            ("999999", BostaApiClient.MAX_RETRY_AFTER_SECONDS),
        ]
        for header, expected_delay in cases:
            client, transport, sleeps = self.make_client([
                FakeResponse(status_code=429, headers={"Retry-After": header}),
                FakeResponse(payload=search_payload([])),
            ])
            client.search_deliveries()
            with self.subTest(header=header):
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(sleeps, [expected_delay])

    def test_timeout_and_connection_failures(self):
        client, transport, sleeps = self.make_client([requests.Timeout("sensitive timeout")])
        with self.assertRaises(BostaApiTimeoutError) as caught:
            client.search_deliveries()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sleeps, [])
        self.assertNotIn("sensitive", str(caught.exception))

        client, transport, sleeps = self.make_client([
            requests.ConnectionError("secret-1"),
            requests.ConnectionError("secret-2"),
            requests.ConnectionError("secret-3"),
        ])
        with self.assertRaises(BostaApiConnectionError) as caught:
            client.search_deliveries()
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertNotIn("secret", str(caught.exception))

    def test_page_and_limit_validation(self):
        client, transport, _ = self.make_client([])
        for page in (0, -1, "1", True):
            with self.subTest(page=page), self.assertRaises(BostaApiConfigurationError):
                client.search_deliveries(page=page)
        for limit in (0, -1, 1501, "1500", True):
            with self.subTest(limit=limit), self.assertRaises(BostaApiConfigurationError):
                client.search_deliveries(limit=limit)
        self.assertEqual(transport.calls, [])

    def test_constructor_rejects_unsafe_page_size(self):
        for page_size in (0, -1, 1501, True, "1500"):
            with self.subTest(page_size=page_size), self.assertRaises(BostaApiConfigurationError):
                BostaApiClient(transport=QueueTransport([]), environ=ENV, page_size=page_size)

    def test_constructor_rejects_unsafe_max_pages(self):
        for max_pages in (0, -1, 10001, True, "10000"):
            with self.subTest(max_pages=max_pages), self.assertRaises(BostaApiConfigurationError):
                BostaApiClient(transport=QueueTransport([]), environ=ENV, max_pages=max_pages)

    def test_iter_all_rejects_unsafe_overrides_before_network(self):
        client, transport, _ = self.make_client([])
        for page_size in (0, -1, 1501, True, "1500"):
            with self.subTest(page_size=page_size), self.assertRaises(BostaApiConfigurationError):
                client.get_all_deliveries(page_size=page_size)
        for max_pages in (0, -1, 10001, True, "10000"):
            with self.subTest(max_pages=max_pages), self.assertRaises(BostaApiConfigurationError):
                client.get_all_deliveries(max_pages=max_pages)
        self.assertEqual(transport.calls, [])
