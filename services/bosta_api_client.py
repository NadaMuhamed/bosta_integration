"""HTTP-only client for the Bosta API.

This module deliberately contains no Odoo order, stock, accounting, or other
business-document logic. Secrets are loaded from the environment only when an
authenticated request is about to be sent.
"""

import hashlib
import json
import os
import time
from urllib.parse import quote, urlsplit

import requests

from .exceptions import (
    BostaApiAuthenticationError,
    BostaApiConfigurationError,
    BostaApiConnectionError,
    BostaApiContractError,
    BostaApiNotFoundError,
    BostaApiPaginationError,
    BostaApiPermissionError,
    BostaApiRateLimitError,
    BostaApiServerError,
    BostaApiTimeoutError,
)


class BostaApiClient:
    SEARCH_PATH = "/api/v2/deliveries/search"
    DETAILS_PATH = "/api/v2/deliveries/business/{tracking_number}"
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    MAX_RETRY_AFTER_SECONDS = 30.0

    def __init__(
        self,
        *,
        base_url="https://app.bosta.co",
        api_key_env_var="BOSTA_API_KEY",
        timeout=30,
        page_size=1500,
        max_pages=10000,
        transport=None,
        sleep=None,
        environ=None,
        max_retries=2,
        backoff_seconds=0.25,
    ):
        self.base_url = self._validate_base_url(base_url)
        self.api_key_env_var = api_key_env_var
        self.timeout = timeout
        self.page_size = self._validate_page_size(page_size)
        self.max_pages = self._validate_max_pages(max_pages)
        self.transport = transport or requests
        self.sleep = sleep or time.sleep
        self.environ = environ if environ is not None else os.environ
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))

    @staticmethod
    def _validate_base_url(value):
        """Validate and normalize the only origin authorized to receive the API key."""
        if not isinstance(value, str):
            raise BostaApiConfigurationError(
                "The Bosta API base URL must be exactly https://app.bosta.co."
            )

        normalized = value.strip()
        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except (TypeError, ValueError):
            raise BostaApiConfigurationError(
                "The Bosta API base URL must be exactly https://app.bosta.co."
            ) from None

        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != "app.bosta.co"
            or parsed.netloc != "app.bosta.co"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise BostaApiConfigurationError(
                "The Bosta API base URL must be exactly https://app.bosta.co."
            )

        return "https://app.bosta.co"

    @staticmethod
    def _validate_page_size(value):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1500:
            raise BostaApiConfigurationError(
                "The Bosta API page size must be between 1 and 1500."
            )
        return value

    @staticmethod
    def _validate_max_pages(value):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10000:
            raise BostaApiConfigurationError(
                "The Bosta API maximum page count must be between 1 and 10000."
            )
        return value

    def _load_api_key(self):
        value = self.environ.get(self.api_key_env_var)
        if not isinstance(value, str) or not value.strip():
            raise BostaApiConfigurationError()
        return value.strip()

    def _headers(self):
        return {
            "Authorization": self._load_api_key(),
            "Content-Type": "application/json",
        }

    def _url(self, path):
        # Revalidate at request time as well as construction time so a mutated
        # client can never load an API key for, or send it to, another origin.
        base_url = self._validate_base_url(self.base_url)
        return f"{base_url}{path}"

    @staticmethod
    def _safe_retry_after(response):
        headers = getattr(response, "headers", None) or {}
        raw_value = headers.get("Retry-After")
        if raw_value is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        return min(value, BostaApiClient.MAX_RETRY_AFTER_SECONDS)

    def _retry_delay(self, response, retry_index):
        retry_after = self._safe_retry_after(response) if response is not None else None
        if retry_after is not None:
            return retry_after
        return min(self.backoff_seconds * (2 ** retry_index), self.MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _raise_http_error(status_code, *, details_request=False):
        if status_code == 401:
            raise BostaApiAuthenticationError()
        if status_code == 403:
            raise BostaApiPermissionError()
        if status_code == 404 and details_request:
            raise BostaApiNotFoundError()
        if status_code == 429:
            raise BostaApiRateLimitError()
        if status_code in {500, 502, 503, 504}:
            raise BostaApiServerError()
        raise BostaApiContractError("The Bosta API rejected the request.")

    def _request(self, method, path, *, json_body=None, details_request=False):
        url = self._url(path)
        headers = self._headers()
        total_attempts = self.max_retries + 1

        for attempt in range(total_attempts):
            response = None
            try:
                response = self.transport.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                raise BostaApiTimeoutError() from None
            except requests.ConnectionError:
                if attempt < self.max_retries:
                    self.sleep(self._retry_delay(None, attempt))
                    continue
                raise BostaApiConnectionError() from None
            except requests.RequestException:
                raise BostaApiConnectionError() from None

            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, int) and 200 <= status_code < 300:
                try:
                    return response.json()
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise BostaApiContractError() from None

            if status_code in self.RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                self.sleep(self._retry_delay(response, attempt))
                continue

            self._raise_http_error(status_code, details_request=details_request)

        raise BostaApiServerError()

    @staticmethod
    def _validate_success_object(payload):
        if not isinstance(payload, dict):
            raise BostaApiContractError()
        # Phase 2R deliberately fails closed on the documented Bosta contract:
        # a successful payload must explicitly contain the boolean marker True.
        if payload.get("success") is not True:
            raise BostaApiContractError("The Bosta API reported an unsuccessful response.")
        return payload

    def search_deliveries(self, page=1, limit=None):
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise BostaApiConfigurationError("The Bosta API page must be a positive integer.")
        if limit is None:
            limit = self.page_size
        limit = self._validate_page_size(limit)

        payload = self._request(
            "POST",
            self.SEARCH_PATH,
            json_body={"page": page, "limit": limit},
        )
        payload = self._validate_success_object(payload)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BostaApiContractError()
        deliveries = data.get("deliveries")
        if not isinstance(deliveries, list):
            raise BostaApiContractError()
        return deliveries, payload

    @staticmethod
    def _delivery_identities(delivery):
        """Return every stable identity available for one logical delivery.

        `_id` and `trackingNumber` are independent defensive identifiers. A
        match on either identifier is enough to treat a later payload as the
        same logical delivery.
        """
        if not isinstance(delivery, dict):
            return ()
        identities = []
        delivery_id = delivery.get("_id")
        tracking_number = delivery.get("trackingNumber")
        if delivery_id not in (None, ""):
            identities.append(("_id", str(delivery_id)))
        if tracking_number not in (None, ""):
            identities.append(("trackingNumber", str(tracking_number)))
        return tuple(identities)

    @classmethod
    def _page_fingerprint(cls, deliveries):
        # Order-insensitive when every entry has at least one stable identity.
        # This catches repeated pages even if Bosta changes list ordering.
        identity_groups = [cls._delivery_identities(item) for item in deliveries]
        if deliveries and all(identity_group for identity_group in identity_groups):
            material = repr(tuple(sorted(identity_groups))).encode("utf-8")
        else:
            try:
                normalized = [
                    json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
                    for item in deliveries
                ]
                material = repr(tuple(sorted(normalized))).encode("utf-8")
            except (TypeError, ValueError):
                material = repr(deliveries).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _pagination_says_last(payload, page, *, page_count=None, limit=None):
        """Return True only for internally consistent completion metadata.

        Bosta has been observed returning a full page while also reporting
        ``count = 0``.  A contradictory count makes completion metadata from
        the same response untrustworthy, so a full page must continue until a
        short/empty page or separately consistent metadata proves completion.
        """
        if not isinstance(payload, dict):
            return False
        candidates = []
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("pagination"))
            candidates.append(data)
        candidates.append(payload.get("pagination"))
        candidates.append(payload)

        full_page = (
            isinstance(page_count, int)
            and isinstance(limit, int)
            and page_count >= limit
        )
        contradictory_count = False
        if full_page:
            for container in (data, payload):
                if not isinstance(container, dict):
                    continue
                for key in ("count", "total", "totalCount", "total_count"):
                    value = container.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        # Any advertised total smaller than the records already
                        # returned on this page is self-contradictory.
                        if value < page_count:
                            contradictory_count = True
                            break
                if contradictory_count:
                    break

        if contradictory_count:
            return False

        for meta in candidates:
            if not isinstance(meta, dict):
                continue
            if meta.get("hasNext") is False or meta.get("has_next") is False:
                return True
            total_pages = meta.get("totalPages", meta.get("total_pages"))
            current_page = meta.get("currentPage", meta.get("current_page", page))
            if isinstance(total_pages, int) and isinstance(current_page, int):
                if total_pages >= 0 and current_page >= total_pages:
                    return True
        return False

    def iter_all_deliveries(self, *, page_size=None, max_pages=None):
        limit = self._validate_page_size(
            self.page_size if page_size is None else page_size
        )
        page_limit = self._validate_max_pages(
            self.max_pages if max_pages is None else max_pages
        )

        # Map each stable identity to a logical-delivery group. A duplicate
        # payload can introduce a new alias (for example the same `_id` with a
        # changed tracking number); registering every alias prevents transitive
        # identity chains from being yielded as new deliveries later.
        identity_to_group = {}
        group_parent = {}
        next_group = 0
        seen_page_fingerprints = set()

        def find(group):
            root = group
            while group_parent[root] != root:
                root = group_parent[root]
            while group_parent[group] != group:
                parent = group_parent[group]
                group_parent[group] = root
                group = parent
            return root

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                group_parent[right_root] = left_root
            return left_root

        for page in range(1, page_limit + 1):
            deliveries, payload = self.search_deliveries(page=page, limit=limit)
            if not deliveries:
                return

            fingerprint = self._page_fingerprint(deliveries)
            if fingerprint in seen_page_fingerprints:
                raise BostaApiPaginationError()
            seen_page_fingerprints.add(fingerprint)

            new_delivery_count = 0
            for delivery in deliveries:
                identities = self._delivery_identities(delivery)
                known_groups = {
                    find(identity_to_group[identity])
                    for identity in identities
                    if identity in identity_to_group
                }

                if known_groups:
                    root = next(iter(known_groups))
                    for group in known_groups:
                        root = union(root, group)

                    # Critical alias propagation: even though this payload is a
                    # duplicate, every identity it contains now belongs to the
                    # same logical delivery and must be remembered for later
                    # pages.
                    for identity in identities:
                        existing_group = identity_to_group.get(identity)
                        if existing_group is not None:
                            root = union(root, existing_group)
                        identity_to_group[identity] = root
                    continue

                if identities:
                    group = next_group
                    next_group += 1
                    group_parent[group] = group
                    for identity in identities:
                        identity_to_group[identity] = group

                new_delivery_count += 1
                yield delivery

            if new_delivery_count == 0:
                raise BostaApiPaginationError(
                    "Bosta delivery pagination made no progress."
                )

            if len(deliveries) < limit or self._pagination_says_last(
                payload, page, page_count=len(deliveries), limit=limit
            ):
                return

        raise BostaApiPaginationError("Bosta delivery pagination reached the configured safety limit.")

    def get_all_deliveries(self, *, page_size=None, max_pages=None):
        return list(self.iter_all_deliveries(page_size=page_size, max_pages=max_pages))

    def get_delivery_details(self, tracking_number):
        if tracking_number is None:
            raise BostaApiConfigurationError("A Bosta tracking number is required.")
        tracking_number = str(tracking_number).strip()
        if not tracking_number or len(tracking_number) > 256 or any(ch in tracking_number for ch in "\r\n\x00"):
            raise BostaApiConfigurationError("A valid Bosta tracking number is required.")
        encoded_tracking = quote(tracking_number, safe="")
        payload = self._request(
            "GET",
            self.DETAILS_PATH.format(tracking_number=encoded_tracking),
            details_request=True,
        )
        payload = self._validate_success_object(payload)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BostaApiContractError()
        return data

    def test_connection(self):
        deliveries, _payload = self.search_deliveries(page=1, limit=1)
        return deliveries
