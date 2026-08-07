"""Pure orchestration between BostaApiClient and Phase 4 normalization."""

from .bosta_delivery_normalizer import (
    normalize_details_delivery,
    normalize_search_delivery,
)


class BostaExtractionService:
    """Extract and normalize Bosta data without persistence or implicit N+1 calls."""

    def __init__(self, client):
        self.client = client

    def iter_normalized_search_deliveries(self, *, page_size=None, max_pages=None):
        kwargs = {}
        if page_size is not None:
            kwargs["page_size"] = page_size
        if max_pages is not None:
            kwargs["max_pages"] = max_pages
        for delivery in self.client.iter_all_deliveries(**kwargs):
            yield normalize_search_delivery(delivery)

    def get_normalized_delivery_details(self, tracking_number):
        payload = self.client.get_delivery_details(tracking_number)
        return normalize_details_delivery(payload)
