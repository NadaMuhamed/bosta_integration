from copy import deepcopy
from datetime import datetime
from unittest import TestCase

from ..services.bosta_delivery_normalizer import (
    normalize_details_delivery,
    normalize_search_delivery,
)


REAL_SHAPE_SEARCH_FIXTURE = {
    "_id": "synthetic-id",
    "trackingNumber": "1234567890",
    "state": {
        "value": "Delivered",
        "code": 45,
        "deliveryTime": "2026-08-06T13:07:54.000Z",
    },
    "specs": {
        "size": "SMALL",
        "weight": 1,
        "packageDetails": {
            "itemsCount": 1,
            "description": "Synthetic package description",
        },
        "packageType": "Small",
    },
    "productInfo": [
        {
            "_id": "synthetic-product",
            "productId": "product-1",
            "title": "Synthetic Product",
            "quantity": 1,
            "productType": "forward",
            "optionsString": "Synthetic Option ",
        }
    ],
    "createdAt": "Thu Aug 06 2026 11:15:31 GMT+0000 (Coordinated Universal Time)",
    "updatedAt": "Thu Aug 06 2026 13:07:54 GMT+0000 (Coordinated Universal Time)",
    "pendingPickup": "2026-08-06T09:27:24.212Z",
}

REAL_SHAPE_DETAILS_FIXTURE = deepcopy(REAL_SHAPE_SEARCH_FIXTURE)
REAL_SHAPE_DETAILS_FIXTURE["state"] = {
    **REAL_SHAPE_DETAILS_FIXTURE["state"],
    "pickedUpTime": "2026-08-06T10:15:31.500Z",
}
REAL_SHAPE_DETAILS_FIXTURE.update(
    {
        "deliveryAttemptsLength": 1,
        "returnAttemptsLength": 0,
        "pickupAttemptsLength": 1,
        "shipmentFees": 83,
        "timeline": [
            {
                "value": "out_for_return",
                "done": True,
                "timestamp": "2026-08-06T13:30:00.000Z",
            },
            {
                "value": "returned_to_origin",
                "done": False,
                "timestamp": "2026-08-06T14:00:00.000Z",
            },
        ],
    }
)


class TestRealBostaPayloadShapes(TestCase):
    def test_real_bosta_created_at_format(self):
        values = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["values"]
        self.assertEqual(values["bosta_created_at"], datetime(2026, 8, 6, 11, 15, 31))

    def test_real_bosta_updated_at_format(self):
        values = normalize_details_delivery(deepcopy(REAL_SHAPE_DETAILS_FIXTURE))["values"]
        self.assertEqual(values["bosta_updated_at"], datetime(2026, 8, 6, 13, 7, 54))

    def test_real_pending_pickup_path(self):
        values = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["values"]
        self.assertEqual(values["pending_pickup_at"], datetime(2026, 8, 6, 9, 27, 24, 212000))

    def test_real_state_delivery_time_path(self):
        values = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["values"]
        self.assertEqual(values["delivery_time"], datetime(2026, 8, 6, 13, 7, 54))

    def test_real_state_picked_up_time_path(self):
        values = normalize_details_delivery(deepcopy(REAL_SHAPE_DETAILS_FIXTURE))["values"]
        self.assertEqual(values["picked_up_at"], datetime(2026, 8, 6, 10, 15, 31, 500000))

    def test_real_specs_package_details_items_count(self):
        values = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["values"]
        self.assertEqual(values["package_items_count"], 1)
        self.assertEqual(values["package_size"], "SMALL")
        self.assertEqual(values["package_weight"], 1)
        self.assertEqual(values["package_type"], "Small")
        result = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))
        self.assertEqual(len(result["items"]), 1)

    def test_real_product_info_product_type(self):
        item = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["items"][0]
        self.assertEqual(item["product_type"], "forward")

    def test_real_product_info_options_string(self):
        item = normalize_search_delivery(deepcopy(REAL_SHAPE_SEARCH_FIXTURE))["items"][0]
        self.assertEqual(item["options_string"], "Synthetic Option")

    def test_real_delivery_attempts_length(self):
        values = normalize_details_delivery(deepcopy(REAL_SHAPE_DETAILS_FIXTURE))["values"]
        self.assertEqual(values["delivery_attempts_count"], 1)

    def test_real_return_attempts_length_zero(self):
        values = normalize_details_delivery(deepcopy(REAL_SHAPE_DETAILS_FIXTURE))["values"]
        self.assertIn("return_attempts_count", values)
        self.assertEqual(values["return_attempts_count"], 0)

    def test_real_pickup_attempts_length(self):
        values = normalize_details_delivery(deepcopy(REAL_SHAPE_DETAILS_FIXTURE))["values"]
        self.assertEqual(values["pickup_attempts_count"], 1)
