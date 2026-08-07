from copy import deepcopy
from datetime import datetime
from unittest import TestCase

from ..services.bosta_delivery_normalizer import (
    merge_normalized_delivery,
    normalize_details_delivery,
    normalize_search_delivery,
)
from ..services.exceptions import BostaDeliveryNormalizationError


def base_payload(**extra):
    payload = {"_id": "delivery-A", "trackingNumber": " 1234567890 "}
    payload.update(extra)
    return payload


class TestBostaApiNormalization(TestCase):
    # IDENTITY
    def test_01_id_maps_to_bosta_delivery_id(self):
        self.assertEqual(normalize_search_delivery(base_payload())["values"]["bosta_delivery_id"], "delivery-A")

    def test_02_tracking_maps_to_tracking_number(self):
        self.assertEqual(normalize_search_delivery(base_payload())["values"]["tracking_number"], "1234567890")

    def test_03_numeric_tracking_becomes_string(self):
        result = normalize_search_delivery(base_payload(trackingNumber=48089608))
        self.assertEqual(result["values"]["tracking_number"], "48089608")

    def test_04_missing_id_fails_safely(self):
        with self.assertRaises(BostaDeliveryNormalizationError):
            normalize_search_delivery({"trackingNumber": "T1"})

    def test_05_missing_tracking_fails_safely(self):
        with self.assertRaises(BostaDeliveryNormalizationError):
            normalize_search_delivery({"_id": "A"})

    def test_06_normalizer_does_not_mutate_input(self):
        payload = base_payload(receiver={"firstName": "Example"}, productInfo=[{"title": "Item"}])
        original = deepcopy(payload)
        normalize_details_delivery(payload)
        self.assertEqual(payload, original)

    # SOURCE
    def test_07_shopify_source_preserved(self):
        self.assertEqual(normalize_search_delivery(base_payload(creationSrc="SHOPIFY"))["values"]["creation_source"], "SHOPIFY")

    def test_08_woocommerce_source_preserved(self):
        self.assertEqual(normalize_search_delivery(base_payload(creationSrc="WOOCOMMERCE"))["values"]["creation_source"], "WOOCOMMERCE")

    def test_09_business_dashboard_source_preserved(self):
        result = normalize_search_delivery(base_payload(creationSrc="BUSINESS_DASHBOARD"))
        self.assertEqual(result["values"]["creation_source"], "BUSINESS_DASHBOARD")

    def test_10_unknown_source_preserved(self):
        result = normalize_search_delivery(base_payload(creationSrc="FUTURE_SOURCE"))
        self.assertEqual(result["values"]["creation_source"], "FUTURE_SOURCE")

    # BUSINESS REFERENCE
    def test_11_business_reference_normalized(self):
        result = normalize_search_delivery(base_payload(businessReference="ref-1067"))
        self.assertEqual(result["values"]["business_reference"], "ref-1067")

    def test_12_unique_business_reference_optional(self):
        result = normalize_search_delivery(base_payload())
        self.assertNotIn("unique_business_reference", result["values"])

    def test_13_same_business_reference_allowed_in_normalized_deliveries(self):
        a = normalize_search_delivery(base_payload(businessReference="same"))
        b = normalize_search_delivery(base_payload(_id="delivery-B", trackingNumber="T2", businessReference="same"))
        self.assertEqual(a["values"]["business_reference"], b["values"]["business_reference"])
        self.assertNotEqual(a["values"]["bosta_delivery_id"], b["values"]["bosta_delivery_id"])

    # TYPE
    def test_14_send_raw_type_preserved(self):
        result = normalize_search_delivery(base_payload(type={"code": 10, "value": "Send"}))
        self.assertEqual((result["values"]["delivery_type_code"], result["values"]["delivery_type_value"]), (10, "Send"))

    def test_15_return_to_origin_raw_type_preserved(self):
        result = normalize_search_delivery(base_payload(type={"code": 20, "value": "Return to Origin"}))
        self.assertEqual(result["values"]["delivery_type_value"], "Return to Origin")

    def test_16_customer_return_raw_type_preserved(self):
        result = normalize_search_delivery(base_payload(type={"code": 25, "value": "Customer Return Pickup"}))
        self.assertEqual(result["values"]["delivery_type_code"], 25)

    def test_17_unknown_future_type_preserved(self):
        result = normalize_search_delivery(base_payload(type={"code": 99, "value": "Future Type"}))
        self.assertEqual(result["values"]["delivery_type_value"], "Future Type")
        self.assertNotIn("flow_type", result["values"])

    # STATE
    def test_18_state_code_value_normalized(self):
        result = normalize_search_delivery(base_payload(state={"code": 45, "value": "Delivered", "childState": "done"}))
        self.assertEqual(result["values"]["state_code"], 45)
        self.assertEqual(result["values"]["state_value"], "Delivered")
        self.assertEqual(result["values"]["state_child_state"], "done")

    def test_19_missing_state_tolerated(self):
        result = normalize_search_delivery(base_payload())
        self.assertNotIn("state_code", result["values"])
        self.assertNotIn("state_value", result["values"])

    def test_20_delivered_can_coexist_with_different_types(self):
        send = normalize_search_delivery(base_payload(type={"code": 10, "value": "Send"}, state={"code": 45, "value": "Delivered"}))
        ret = normalize_search_delivery(base_payload(_id="B", trackingNumber="T2", type={"code": 25, "value": "Customer Return Pickup"}, state={"code": 46, "value": "Delivered"}))
        self.assertEqual(send["values"]["state_value"], ret["values"]["state_value"])
        self.assertNotEqual(send["values"]["delivery_type_code"], ret["values"]["delivery_type_code"])

    # DATES
    def test_21_trailing_z_is_parsed(self):
        result = normalize_search_delivery(base_payload(createdAt="2026-08-07T20:30:40.123456Z"))
        self.assertEqual(result["values"]["bosta_created_at"], datetime(2026, 8, 7, 20, 30, 40, 123456))

    def test_22_offset_timestamp_converts_to_utc(self):
        result = normalize_search_delivery(base_payload(updatedAt="2026-08-07T23:30:00+03:00"))
        self.assertEqual(result["values"]["bosta_updated_at"], datetime(2026, 8, 7, 20, 30, 0))

    def test_23_absent_date_omitted(self):
        result = normalize_search_delivery(base_payload())
        self.assertNotIn("bosta_created_at", result["values"])

    def test_24_malformed_optional_date_omitted_safely(self):
        result = normalize_search_delivery(base_payload(createdAt="not-a-date"))
        self.assertNotIn("bosta_created_at", result["values"])

    # RECEIVER
    def test_25_receiver_fields_normalize(self):
        result = normalize_search_delivery(base_payload(receiver={"_id": "R1", "fullName": "Synthetic User", "phone": "000", "secondPhone": "111"}))
        self.assertEqual(result["values"]["receiver_bosta_id"], "R1")
        self.assertEqual(result["values"]["receiver_name"], "Synthetic User")
        self.assertEqual(result["values"]["receiver_phone"], "000")
        self.assertEqual(result["values"]["receiver_second_phone"], "111")

    def test_26_receiver_absent_is_valid(self):
        result = normalize_search_delivery(base_payload())
        self.assertNotIn("receiver_name", result["values"])

    def test_27_first_last_names_form_receiver_name(self):
        result = normalize_search_delivery(base_payload(receiver={"firstName": "Synthetic", "lastName": "Person"}))
        self.assertEqual(result["values"]["receiver_name"], "Synthetic Person")

    # ADDRESS
    def test_28_complete_address_normalizes(self):
        address = {
            "country": {"code": "EG", "name": "Egypt"},
            "city": {"name": "City"}, "zone": {"name": "Zone"}, "district": "District",
            "firstLine": "Line 1", "secondLine": "Line 2", "buildingNumber": "10", "floor": "2", "apartment": "3",
        }
        result = normalize_search_delivery(base_payload(dropOffAddress=address))["values"]
        self.assertEqual(result["dropoff_country_code"], "EG")
        self.assertEqual(result["dropoff_country_name"], "Egypt")
        self.assertEqual(result["dropoff_city"], "City")
        self.assertEqual(result["dropoff_apartment"], "3")

    def test_29_partial_address_normalizes(self):
        result = normalize_search_delivery(base_payload(dropOffAddress={"city": "Cairo"}))["values"]
        self.assertEqual(result["dropoff_city"], "Cairo")
        self.assertNotIn("dropoff_zone", result)

    def test_30_missing_address_valid(self):
        result = normalize_search_delivery(base_payload())["values"]
        self.assertNotIn("dropoff_city", result)

    # COD
    def test_31_cod_zero_preserved(self):
        result = normalize_search_delivery(base_payload(cod=0))["values"]
        self.assertIn("cod_amount", result)
        self.assertEqual(result["cod_amount"], 0.0)

    def test_32_missing_cod_omitted(self):
        self.assertNotIn("cod_amount", normalize_search_delivery(base_payload())["values"])

    def test_33_original_cod_separate(self):
        result = normalize_details_delivery(base_payload(cod=0, originalCod=250))["values"]
        self.assertEqual(result["cod_amount"], 0.0)
        self.assertEqual(result["original_cod_amount"], 250.0)

    # PRICING
    def test_34_empty_pricing_creates_no_fake_zero_fees(self):
        values = normalize_search_delivery(base_payload(pricing={}))["values"]
        for key in ("shipping_fee", "bundle_discount", "opening_package_fee", "price_before_vat", "price_after_vat"):
            self.assertNotIn(key, values)

    def test_35_shipment_fees_normalized(self):
        self.assertEqual(normalize_details_delivery(base_payload(shipmentFees=7.98))["values"]["shipment_fees"], 7.98)

    def test_36_shipping_fee_normalized(self):
        self.assertEqual(normalize_details_delivery(base_payload(pricing={"shippingFee": 83}))["values"]["shipping_fee"], 83.0)

    def test_37_bundle_discount_normalized(self):
        self.assertEqual(normalize_details_delivery(base_payload(pricing={"bundleDiscount": -5}))["values"]["bundle_discount"], -5.0)

    def test_38_opening_package_fee_normalized(self):
        self.assertEqual(normalize_details_delivery(base_payload(pricing={"openingPackageFee": 7}))["values"]["opening_package_fee"], 7.0)

    def test_39_material_fee_normalized(self):
        self.assertEqual(normalize_details_delivery(base_payload(pricing={"bostaMaterialFee": 2}))["values"]["bosta_material_fee"], 2.0)

    def test_40_vat_pre_post_values_normalize(self):
        values = normalize_details_delivery(base_payload(pricing={"vatRate": 14, "priceBeforeVat": 100, "priceAfterVat": 114}))["values"]
        self.assertEqual((values["vat_rate"], values["price_before_vat"], values["price_after_vat"]), (14.0, 100.0, 114.0))

    def test_41_dict_amount_monetary_shape(self):
        values = normalize_details_delivery(base_payload(pricing={"shippingFee": {"amount": 7}}))["values"]
        self.assertEqual(values["shipping_fee"], 7.0)

    def test_42_numeric_monetary_shape(self):
        values = normalize_details_delivery(base_payload(pricing={"shippingFee": "7.5"}))["values"]
        self.assertEqual(values["shipping_fee"], 7.5)

    def test_43_nan_infinity_and_bool_are_omitted(self):
        for bad in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(bad=repr(bad)):
                values = normalize_details_delivery(base_payload(pricing={"shippingFee": bad}))["values"]
                self.assertNotIn("shipping_fee", values)

    # PRODUCTS
    def test_44_absent_product_info_means_none(self):
        self.assertIsNone(normalize_search_delivery(base_payload())["items"])

    def test_45_explicit_empty_product_info_means_empty_list(self):
        self.assertEqual(normalize_search_delivery(base_payload(productInfo=[]))["items"], [])

    def test_46_one_item_normalizes(self):
        item = {"_id": "PI1", "productId": "P1", "title": "Synthetic Item", "quantity": 2, "type": "variant", "options": {"size": "M"}}
        result = normalize_search_delivery(base_payload(productInfo=[item]))["items"][0]
        self.assertEqual(result["bosta_product_info_id"], "PI1")
        self.assertEqual(result["external_product_id"], "P1")
        self.assertEqual(result["quantity"], 2.0)
        self.assertNotIn("delivery_id", result)
        self.assertNotIn("company_id", result)

    def test_47_multiple_items_normalize(self):
        result = normalize_search_delivery(base_payload(productInfo=[{"title": "A"}, {"title": "B"}]))["items"]
        self.assertEqual([item["title"] for item in result], ["A", "B"])
        self.assertEqual([item["sequence"] for item in result], [10, 20])

    def test_48_missing_item_quantity_defaults_to_one(self):
        item = normalize_search_delivery(base_payload(productInfo=[{"title": "A"}]))["items"][0]
        self.assertEqual(item["quantity"], 1.0)

    def test_49_zero_quantity_preserved(self):
        item = normalize_search_delivery(base_payload(productInfo=[{"title": "A", "quantity": 0}]))["items"][0]
        self.assertEqual(item["quantity"], 0.0)

    def test_50_negative_quantity_rejected(self):
        with self.assertRaises(BostaDeliveryNormalizationError):
            normalize_search_delivery(base_payload(productInfo=[{"title": "A", "quantity": -1}]))

    def test_51_malformed_quantity_does_not_default(self):
        with self.assertRaises(BostaDeliveryNormalizationError):
            normalize_search_delivery(base_payload(productInfo=[{"title": "A", "quantity": "bad"}]))

    def test_52_package_description_does_not_create_fake_product(self):
        result = normalize_search_delivery(base_payload(package={"description": "not a product"}))
        self.assertIsNone(result["items"])

    # TIMELINE
    def test_53_absent_timeline_is_none(self):
        self.assertIsNone(normalize_details_delivery(base_payload())["timeline"])

    def test_54_explicit_empty_timeline_is_empty(self):
        self.assertEqual(normalize_details_delivery(base_payload(timeline=[]))["timeline"], [])

    def test_55_timeline_event_normalizes_minimally(self):
        event = {"code": 41, "value": "Picked Up", "done": True, "timestamp": "2026-08-07T20:00:00Z", "staff": {"secret": "discard"}}
        result = normalize_details_delivery(base_payload(timeline=[event]))["timeline"][0]
        self.assertEqual(result, {"code": 41, "value": "Picked Up", "done": True, "timestamp": datetime(2026, 8, 7, 20, 0)})

    def test_56_timeline_done_false_preserved(self):
        result = normalize_details_delivery(base_payload(timeline=[{"value": "returned_to_origin", "done": False}]))["timeline"][0]
        self.assertIs(result["done"], False)

    def test_57_out_for_return_and_returned_to_origin_remain_distinct(self):
        timeline = normalize_details_delivery(base_payload(timeline=[
            {"value": "out_for_return", "done": True},
            {"value": "returned_to_origin", "done": False},
        ]))["timeline"]
        self.assertEqual([(event["value"], event["done"]) for event in timeline], [("out_for_return", True), ("returned_to_origin", False)])

    def test_58_no_lifecycle_or_stock_conclusion_generated(self):
        result = normalize_details_delivery(base_payload(timeline=[{"value": "out_for_return", "done": True}]))
        rendered = repr(result).lower()
        self.assertNotIn("stock_restored", rendered)
        self.assertNotIn("return_complete", rendered)

    # PARTIAL UPDATE / MERGE
    def test_59_absent_optional_fields_are_omitted(self):
        values = normalize_search_delivery(base_payload())["values"]
        self.assertEqual(set(values), {"bosta_delivery_id", "tracking_number"})

    def test_60_explicit_zero_fields_are_preserved(self):
        values = normalize_search_delivery(base_payload(cod=0, attemptsCount=0))["values"]
        self.assertEqual(values["cod_amount"], 0.0)
        self.assertEqual(values["attempts_count"], 0)

    def test_61_empty_pricing_does_not_erase_enriched_pricing_through_merge(self):
        details = normalize_details_delivery(base_payload(pricing={"shippingFee": 83}))
        search = normalize_search_delivery(base_payload(pricing={}))
        merged = merge_normalized_delivery(details, search)
        self.assertEqual(merged["values"]["shipping_fee"], 83.0)

    def test_62_items_none_preserves_existing_items(self):
        base = normalize_details_delivery(base_payload(productInfo=[{"title": "A"}]))
        enrichment = normalize_search_delivery(base_payload())
        self.assertEqual(merge_normalized_delivery(base, enrichment)["items"], base["items"])

    def test_63_items_empty_explicitly_replaces_existing_items(self):
        base = normalize_details_delivery(base_payload(productInfo=[{"title": "A"}]))
        enrichment = normalize_search_delivery(base_payload(productInfo=[]))
        self.assertEqual(merge_normalized_delivery(base, enrichment)["items"], [])

    def test_64_contradictory_delivery_ids_fail_merge(self):
        base = normalize_search_delivery(base_payload())
        enrichment = normalize_details_delivery(base_payload(_id="B"))
        with self.assertRaises(BostaDeliveryNormalizationError):
            merge_normalized_delivery(base, enrichment)

    def test_65_contradictory_tracking_numbers_fail_merge(self):
        base = normalize_search_delivery(base_payload())
        enrichment = normalize_details_delivery(base_payload(trackingNumber="T2"))
        with self.assertRaises(BostaDeliveryNormalizationError):
            merge_normalized_delivery(base, enrichment)


class TestBostaAdditionalNormalizationCoverage(TestCase):
    def test_shopify_info_normalizes_without_becoming_required(self):
        values = normalize_search_delivery(base_payload(shopifyInfo={
            "orderId": 123,
            "orderNumber": "#1001",
            "storeName": "Synthetic Store",
            "createdAt": "2026-08-07T20:00:00Z",
        }))["values"]
        self.assertEqual(values["shopify_order_id"], "123")
        self.assertEqual(values["shopify_order_number"], "#1001")
        self.assertEqual(values["shopify_store_name"], "Synthetic Store")
        self.assertEqual(values["shopify_created_at"], datetime(2026, 8, 7, 20, 0))
        without = normalize_search_delivery(base_payload(creationSrc="WOOCOMMERCE"))["values"]
        self.assertNotIn("shopify_order_id", without)

    def test_package_specs_and_attempts_normalize_without_fake_zeros(self):
        values = normalize_search_delivery(base_payload(
            type={"code": 10, "value": "Send"},
            specs={"packageItemsCount": 2, "packageType": "Parcel", "packageSize": "M", "packageWeight": "1.5"},
            attempts={"count": 0, "delivery": 2, "return": 1, "pickup": 3},
        ))["values"]
        self.assertEqual(values["package_items_count"], 2)
        self.assertEqual(values["package_type"], "Parcel")
        self.assertEqual(values["package_size"], "M")
        self.assertEqual(values["package_weight"], 1.5)
        self.assertEqual(values["attempts_count"], 0)
        self.assertEqual(values["delivery_attempts_count"], 2)
        self.assertEqual(values["return_attempts_count"], 1)
        self.assertEqual(values["pickup_attempts_count"], 3)

    def test_masked_state_normalizes_only_when_present(self):
        values = normalize_search_delivery(base_payload(maskedState="Successful"))["values"]
        self.assertEqual(values["masked_state"], "Successful")

    def test_timeline_none_preserves_base_timeline_during_merge(self):
        base = normalize_details_delivery(base_payload(timeline=[{"value": "Picked Up", "done": True}]))
        enrichment = normalize_search_delivery(base_payload())
        self.assertEqual(merge_normalized_delivery(base, enrichment)["timeline"], base["timeline"])

    def test_explicit_empty_timeline_replaces_base_timeline(self):
        base = normalize_details_delivery(base_payload(timeline=[{"value": "Picked Up", "done": True}]))
        enrichment = normalize_details_delivery(base_payload(timeline=[]))
        self.assertEqual(merge_normalized_delivery(base, enrichment)["timeline"], [])

    def test_explicit_zero_overwrites_previous_nonzero_during_merge(self):
        base = normalize_details_delivery(base_payload(cod=100))
        enrichment = normalize_search_delivery(base_payload(cod=0))
        self.assertEqual(merge_normalized_delivery(base, enrichment)["values"]["cod_amount"], 0.0)

    def test_pricing_currency_code_normalizes(self):
        values = normalize_details_delivery(base_payload(pricing={"currency": {"code": "EGP"}}))["values"]
        self.assertEqual(values["pricing_currency_code"], "EGP")

    def test_null_product_info_is_unavailable_not_explicit_empty(self):
        self.assertIsNone(normalize_search_delivery(base_payload(productInfo=None))["items"])

    def test_null_timeline_is_unavailable_not_explicit_empty(self):
        self.assertIsNone(normalize_details_delivery(base_payload(timeline=None))["timeline"])

    def test_boolean_tracking_identity_is_rejected(self):
        with self.assertRaises(BostaDeliveryNormalizationError):
            normalize_search_delivery(base_payload(trackingNumber=True))
