"""Pure normalization helpers for Bosta delivery payloads.

Phase 4 deliberately stops at transient, model-ready Python structures.  This
module performs no HTTP requests, reads no secrets, and has no Odoo/ORM
imports.  Optional fields follow patch semantics: absence means omission while
an explicitly supplied zero is preserved.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import re

from .exceptions import BostaDeliveryNormalizationError


_MISSING = object()


def _mapping(value):
    return value if isinstance(value, dict) else None


def _present(mapping, key):
    return isinstance(mapping, dict) and key in mapping


def _clean_string(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    return text or None


def _identity_string(payload, key, label):
    if not isinstance(payload, dict):
        raise BostaDeliveryNormalizationError("Bosta delivery payload must be an object.")
    if key not in payload:
        raise BostaDeliveryNormalizationError(f"Bosta delivery {label} is required.")
    raw_value = payload.get(key)
    if isinstance(raw_value, bool):
        raise BostaDeliveryNormalizationError(f"Bosta delivery {label} is required.")
    value = _clean_string(raw_value)
    if value is None:
        raise BostaDeliveryNormalizationError(f"Bosta delivery {label} is required.")
    return value


def _finite_number(value):
    """Return a finite int/float or None for malformed optional numeric data."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(float(number)):
        return None
    return number


def _integer(value):
    number = _finite_number(value)
    if number is None:
        return None
    numeric = float(number)
    if not numeric.is_integer():
        return None
    return int(numeric)


def _money(value):
    if isinstance(value, dict):
        if "amount" not in value:
            return None
        value = value.get("amount")
    number = _finite_number(value)
    if number is None:
        return None
    return float(number)


_BOSTA_JS_DATETIME_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(\d{2}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) "
    r"GMT([+-])(\d{2})(\d{2}) \(Coordinated Universal Time\)$"
)
_BOSTA_JS_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_bosta_js_datetime(text):
    """Parse Bosta's observed JavaScript Date string without locale dependence."""
    match = _BOSTA_JS_DATETIME_RE.fullmatch(text)
    if not match:
        return None
    (
        _weekday, month_name, day, year, hour, minute, second,
        offset_sign, offset_hour, offset_minute,
    ) = match.groups()
    offset_minutes = int(offset_hour) * 60 + int(offset_minute)
    if offset_sign == "-":
        offset_minutes = -offset_minutes
    try:
        parsed = datetime(
            int(year),
            _BOSTA_JS_MONTHS[month_name],
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=timezone(timedelta(minutes=offset_minutes)),
        )
    except (ValueError, OverflowError):
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _datetime(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    js_parsed = _parse_bosta_js_datetime(text)
    if js_parsed is not None:
        return js_parsed

    iso_text = text
    if iso_text.endswith(("Z", "z")):
        iso_text = iso_text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _component(value, *, keys=("name", "value", "code")):
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                text = _clean_string(value.get(key))
                if text is not None:
                    return text
        return None
    return _clean_string(value)


def _put_string(values, target, mapping, source):
    if _present(mapping, source):
        value = _clean_string(mapping[source])
        if value is not None:
            values[target] = value


def _put_number(values, target, mapping, source, *, integer=False):
    if _present(mapping, source):
        value = _integer(mapping[source]) if integer else _finite_number(mapping[source])
        if value is not None:
            values[target] = value


def _put_money(values, target, mapping, source):
    if _present(mapping, source):
        value = _money(mapping[source])
        if value is not None:
            values[target] = value


def _put_datetime(values, target, mapping, source):
    if _present(mapping, source):
        value = _datetime(mapping[source])
        if value is not None:
            values[target] = value


def _first_mapping(payload, keys):
    for key in keys:
        if _present(payload, key) and isinstance(payload[key], dict):
            return payload[key]
    return None


def _normalize_shopify(values, payload):
    shopify = _mapping(payload.get("shopifyInfo"))
    if shopify is None:
        return
    _put_string(values, "shopify_order_id", shopify, "orderId")
    _put_string(values, "shopify_order_number", shopify, "orderNumber")
    _put_string(values, "shopify_store_name", shopify, "storeName")
    _put_datetime(values, "shopify_created_at", shopify, "createdAt")


def _normalize_type(values, payload):
    node = _mapping(payload.get("type"))
    if node is None:
        return
    _put_number(values, "delivery_type_code", node, "code", integer=True)
    _put_string(values, "delivery_type_value", node, "value")


def _normalize_state(values, payload):
    node = _mapping(payload.get("state"))
    if node is not None:
        _put_number(values, "state_code", node, "code", integer=True)
        _put_string(values, "state_value", node, "value")
        if _present(node, "childState"):
            child = _component(node.get("childState"))
            if child is not None:
                values["state_child_state"] = child
    _put_string(values, "masked_state", payload, "maskedState")


def _normalize_dates(values, payload):
    explicit_paths = (
        ("createdAt", "bosta_created_at"),
        ("updatedAt", "bosta_updated_at"),
        ("pendingPickup", "pending_pickup_at"),
        ("pendingPickupDate", "pending_pickup_at"),
        ("pendingPickupAt", "pending_pickup_at"),
        ("collectedFromBusiness", "collected_from_business_at"),
        ("collectedFromBusinessAt", "collected_from_business_at"),
        ("pickedUpAt", "picked_up_at"),
        ("pickedUpDate", "picked_up_at"),
        ("deliveryTime", "delivery_time"),
        ("deliveredAt", "delivery_time"),
    )
    assigned = set()
    for source, target in explicit_paths:
        if target in assigned or not _present(payload, source):
            continue
        parsed = _datetime(payload[source])
        if parsed is not None:
            values[target] = parsed
            assigned.add(target)

    state = _mapping(payload.get("state"))
    if state is not None:
        for source, target in (
            ("deliveryTime", "delivery_time"),
            ("pickedUpTime", "picked_up_at"),
        ):
            if target in assigned or not _present(state, source):
                continue
            parsed = _datetime(state[source])
            if parsed is not None:
                values[target] = parsed
                assigned.add(target)


def _normalize_receiver(values, payload):
    receiver = _mapping(payload.get("receiver"))
    if receiver is None:
        return
    _put_string(values, "receiver_bosta_id", receiver, "_id")
    _put_string(values, "receiver_phone", receiver, "phone")
    _put_string(values, "receiver_second_phone", receiver, "secondPhone")

    full_name = None
    for key in ("fullName", "name"):
        if _present(receiver, key):
            full_name = _clean_string(receiver[key])
            if full_name:
                break
    if not full_name:
        first = _clean_string(receiver.get("firstName"))
        last = _clean_string(receiver.get("lastName"))
        combined = " ".join(part for part in (first, last) if part)
        full_name = combined or None
    if full_name:
        values["receiver_name"] = full_name


def _normalize_address(values, payload):
    address = _first_mapping(payload, ("dropOffAddress", "dropoffAddress"))
    if address is None:
        return

    country = address.get("country", _MISSING)
    if country is not _MISSING:
        if isinstance(country, dict):
            code = _component(country, keys=("code", "countryCode", "value"))
            name = _component(country, keys=("name", "countryName", "value"))
            if code is not None:
                values["dropoff_country_code"] = code
            if name is not None:
                values["dropoff_country_name"] = name
        else:
            text = _clean_string(country)
            if text is not None:
                values["dropoff_country_name"] = text

    if _present(address, "countryCode"):
        text = _component(address["countryCode"], keys=("code", "value", "name"))
        if text is not None:
            values["dropoff_country_code"] = text
    if _present(address, "countryName"):
        text = _component(address["countryName"], keys=("name", "value", "code"))
        if text is not None:
            values["dropoff_country_name"] = text

    for source, target in (
        ("city", "dropoff_city"),
        ("zone", "dropoff_zone"),
        ("district", "dropoff_district"),
        ("firstLine", "dropoff_first_line"),
        ("secondLine", "dropoff_second_line"),
        ("buildingNumber", "dropoff_building_number"),
        ("floor", "dropoff_floor"),
        ("apartment", "dropoff_apartment"),
        ("apartmentNumber", "dropoff_apartment"),
    ):
        if target in values or not _present(address, source):
            continue
        text = _component(address[source])
        if text is not None:
            values[target] = text


def _normalize_package(values, payload):
    # Bosta payloads have used both top-level package-prefixed fields and
    # `specs`/`package` containers. These are explicit paths only.
    nested_nodes = []
    for key in ("specs", "package"):
        node = _mapping(payload.get(key))
        if node is not None:
            nested_nodes.append(node)

    top_level = (
        ("packageItemsCount", "package_items_count", "integer"),
        ("packageType", "package_type", "string"),
        ("packageSize", "package_size", "string"),
        ("packageWeight", "package_weight", "number"),
    )
    for source, target, kind in top_level:
        if not _present(payload, source):
            continue
        raw = payload[source]
        value = _integer(raw) if kind == "integer" else (
            _finite_number(raw) if kind == "number" else _clean_string(raw)
        )
        if value is not None:
            values[target] = value

    nested_mappings = (
        (("packageItemsCount", "itemsCount", "numberOfItems"), "package_items_count", "integer"),
        (("packageType", "type"), "package_type", "string"),
        (("packageSize", "size"), "package_size", "string"),
        (("packageWeight", "weight"), "package_weight", "number"),
    )
    for sources, target, kind in nested_mappings:
        if target in values:
            continue
        for node in nested_nodes:
            matched = False
            for source in sources:
                if not _present(node, source):
                    continue
                raw = node[source]
                value = _integer(raw) if kind == "integer" else (
                    _finite_number(raw) if kind == "number" else _clean_string(raw)
                )
                if value is not None:
                    values[target] = value
                matched = True
                break
            if matched:
                break

    if "package_items_count" not in values:
        specs = _mapping(payload.get("specs"))
        package_details = _mapping(specs.get("packageDetails")) if specs is not None else None
        if package_details is not None and _present(package_details, "itemsCount"):
            value = _integer(package_details["itemsCount"])
            if value is not None:
                values["package_items_count"] = value

    # packageDetails.description is not authoritative productInfo, but Phase 7
    # persists the original text as conservative mapping/inventory evidence.
    specs = _mapping(payload.get("specs"))
    package_details = _mapping(specs.get("packageDetails")) if specs is not None else None
    if package_details is not None and _present(package_details, "description"):
        description = _clean_string(package_details.get("description"))
        if description is not None:
            values["package_description"] = description

    attempt_nodes = [payload]
    attempts = _mapping(payload.get("attempts"))
    if attempts is not None:
        attempt_nodes.append(attempts)
    for sources, target in (
        (("attemptsCount", "numberOfAttempts", "count"), "attempts_count"),
        (("deliveryAttemptsLength", "deliveryAttemptsCount", "deliveryAttempts", "delivery"), "delivery_attempts_count"),
        (("returnAttemptsLength", "returnAttemptsCount", "returnAttempts", "return"), "return_attempts_count"),
        (("pickupAttemptsLength", "pickupAttemptsCount", "pickupAttempts", "pickup"), "pickup_attempts_count"),
    ):
        for node in attempt_nodes:
            matched = False
            for source in sources:
                if _present(node, source):
                    value = _integer(node[source])
                    if value is not None:
                        values[target] = value
                    matched = True
                    break
            if matched:
                break


def _normalize_cod(values, payload):
    _put_money(values, "cod_amount", payload, "cod")
    for source in ("originalCod", "originalCOD", "originalCodAmount"):
        if _present(payload, source):
            value = _money(payload[source])
            if value is not None:
                values["original_cod_amount"] = value
            break


def _normalize_pricing(values, payload):
    _put_money(values, "shipment_fees", payload, "shipmentFees")
    pricing = _mapping(payload.get("pricing"))
    if not pricing:
        return
    for source, target in (
        ("shippingFee", "shipping_fee"),
        ("bundleDiscount", "bundle_discount"),
        ("openingPackageFee", "opening_package_fee"),
        ("bostaMaterialFee", "bosta_material_fee"),
        ("priceBeforeVat", "price_before_vat"),
        ("priceAfterVat", "price_after_vat"),
        ("vatRate", "vat_rate"),
        ("vat", "vat_rate"),
    ):
        if target in values or not _present(pricing, source):
            continue
        value = _money(pricing[source])
        if value is not None:
            values[target] = value

    for source in ("currencyCode", "currency"):
        if _present(pricing, source):
            raw = pricing[source]
            if isinstance(raw, dict):
                text = _component(raw, keys=("code", "value", "name"))
            else:
                text = _clean_string(raw)
            if text is not None:
                values["pricing_currency_code"] = text
            break


def _normalize_item(item, sequence):
    if not isinstance(item, dict):
        raise BostaDeliveryNormalizationError("Bosta productInfo item must be an object.")
    result = {"sequence": sequence}

    for source in ("_id", "id"):
        if _present(item, source):
            text = _clean_string(item[source])
            if text is not None:
                result["bosta_product_info_id"] = text
            break
    _put_string(result, "external_product_id", item, "productId")
    if _present(item, "title"):
        text = _clean_string(item["title"])
        if text is not None:
            result["title"] = text
    elif _present(item, "name"):
        text = _clean_string(item["name"])
        if text is not None:
            result["title"] = text

    if _present(item, "quantity"):
        quantity = _finite_number(item["quantity"])
        if quantity is None:
            raise BostaDeliveryNormalizationError("Bosta product quantity is invalid.")
        quantity = float(quantity)
    else:
        quantity = 1.0
    if quantity < 0:
        raise BostaDeliveryNormalizationError("Bosta product quantity cannot be negative.")
    result["quantity"] = quantity

    if _present(item, "productType"):
        _put_string(result, "product_type", item, "productType")
    else:
        _put_string(result, "product_type", item, "type")

    if _present(item, "optionsString"):
        text = _clean_string(item["optionsString"])
        if text is not None:
            result["options_string"] = text
    elif _present(item, "options"):
        options = item["options"]
        if isinstance(options, str):
            text = options.strip()
        elif isinstance(options, (dict, list)):
            try:
                text = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                text = ""
        else:
            text = _clean_string(options) or ""
        if text:
            result["options_string"] = text
    elif _present(item, "variants"):
        variants = item["variants"]
        if isinstance(variants, (dict, list)):
            try:
                text = json.dumps(variants, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                text = ""
        else:
            text = _clean_string(variants) or ""
        if text:
            result["options_string"] = text
    return result


def _normalize_items(payload):
    if "productInfo" not in payload:
        return None
    product_info = payload.get("productInfo")
    if product_info is None:
        # Null means unavailable/unspecified, not an explicit empty item set.
        return None
    if not isinstance(product_info, list):
        raise BostaDeliveryNormalizationError("Bosta productInfo must be a list when supplied.")
    return [_normalize_item(item, (index + 1) * 10) for index, item in enumerate(product_info)]


def _timeline_event(event):
    if not isinstance(event, dict):
        return None
    state = _mapping(event.get("state"))
    code = None
    value = None
    if state is not None:
        if _present(state, "code"):
            code = _integer(state.get("code"))
        if _present(state, "value"):
            value = _clean_string(state.get("value"))
    if code is None:
        for key in ("code", "stateCode"):
            if _present(event, key):
                code = _integer(event[key])
                break
    if value is None:
        for key in ("value", "stateValue", "name"):
            if _present(event, key):
                value = _clean_string(event[key])
                if value is not None:
                    break

    done = None
    if _present(event, "done") and isinstance(event["done"], bool):
        done = event["done"]
    elif _present(event, "isDone") and isinstance(event["isDone"], bool):
        done = event["isDone"]

    timestamp = None
    for key in ("timestamp", "timeStamp", "createdAt", "updatedAt", "date"):
        if _present(event, key):
            timestamp = _datetime(event[key])
            break

    result = {"code": code, "value": value, "done": done, "timestamp": timestamp}
    if all(value is None for value in result.values()):
        return None
    return result


def _normalize_timeline(payload):
    timeline_key = None
    for key in ("timeline", "stateTimeline", "stateHistory"):
        if key in payload:
            timeline_key = key
            break
    if timeline_key is None:
        return None
    raw_timeline = payload.get(timeline_key)
    if raw_timeline is None:
        # Preserve existing enrichment when the API supplies null rather than
        # an explicit empty list.
        return None
    if not isinstance(raw_timeline, list):
        return []
    result = []
    for raw_event in raw_timeline:
        event = _timeline_event(raw_event)
        if event is not None:
            result.append(event)
    return result


def _normalize(delivery_payload, *, source_kind):
    if not isinstance(delivery_payload, dict):
        raise BostaDeliveryNormalizationError("Bosta delivery payload must be an object.")

    values = {
        "bosta_delivery_id": _identity_string(delivery_payload, "_id", "ID"),
        "tracking_number": _identity_string(delivery_payload, "trackingNumber", "tracking number"),
    }

    _put_string(values, "creation_source", delivery_payload, "creationSrc")
    _put_string(values, "business_reference", delivery_payload, "businessReference")
    _put_string(values, "unique_business_reference", delivery_payload, "uniqueBusinessReference")
    _normalize_shopify(values, delivery_payload)
    _normalize_type(values, delivery_payload)
    _normalize_state(values, delivery_payload)
    _normalize_dates(values, delivery_payload)
    _normalize_receiver(values, delivery_payload)
    _normalize_address(values, delivery_payload)
    _normalize_package(values, delivery_payload)
    _normalize_cod(values, delivery_payload)
    _normalize_pricing(values, delivery_payload)

    return {
        "values": values,
        "items": _normalize_items(delivery_payload),
        "timeline": _normalize_timeline(delivery_payload) if source_kind == "details" else None,
        "source_kind": source_kind,
    }


def normalize_search_delivery(delivery_payload):
    """Normalize one Search API delivery without mutating *delivery_payload*."""
    return _normalize(delivery_payload, source_kind="search")


def normalize_details_delivery(delivery_payload):
    """Normalize one Details API delivery without mutating *delivery_payload*."""
    return _normalize(delivery_payload, source_kind="details")


def merge_normalized_delivery(base, enrichment):
    """Purely merge two normalized envelopes using patch semantics."""
    if not isinstance(base, dict) or not isinstance(enrichment, dict):
        raise BostaDeliveryNormalizationError("Normalized Bosta delivery envelopes are required.")
    base_values = base.get("values")
    enrichment_values = enrichment.get("values")
    if not isinstance(base_values, dict) or not isinstance(enrichment_values, dict):
        raise BostaDeliveryNormalizationError("Normalized Bosta delivery values are required.")

    for key, label in (
        ("bosta_delivery_id", "delivery ID"),
        ("tracking_number", "tracking number"),
    ):
        left = base_values.get(key)
        right = enrichment_values.get(key)
        if left is not None and right is not None and str(left) != str(right):
            raise BostaDeliveryNormalizationError(f"Bosta {label} mismatch prevents merge.")

    merged_values = deepcopy(base_values)
    merged_values.update(deepcopy(enrichment_values))

    enrichment_items = enrichment.get("items", None)
    merged_items = deepcopy(base.get("items")) if enrichment_items is None else deepcopy(enrichment_items)
    enrichment_timeline = enrichment.get("timeline", None)
    merged_timeline = (
        deepcopy(base.get("timeline"))
        if enrichment_timeline is None
        else deepcopy(enrichment_timeline)
    )

    return {
        "values": merged_values,
        "items": merged_items,
        "timeline": merged_timeline,
        "source_kind": enrichment.get("source_kind") or base.get("source_kind"),
    }
