"""Idempotent persistence for normalized Bosta deliveries.

Phase 6 delegates lifecycle interpretation to the pure lifecycle interpreter,
then persists only the derived lifecycle fields alongside Phase 5 data. This
service still contains no HTTP, normalization mapping, stock, business-document,
return-linking, or financial/accounting behavior.
"""

from .bosta_lifecycle_interpreter import BostaLifecycleInterpreter
from .exceptions import (
    BostaPersistenceDataError,
    BostaPersistenceIdentityConflict,
)


DELIVERY_VALUE_FIELDS = frozenset({
    "bosta_delivery_id",
    "tracking_number",
    "creation_source",
    "business_reference",
    "unique_business_reference",
    "shopify_order_id",
    "shopify_order_number",
    "shopify_store_name",
    "shopify_created_at",
    "delivery_type_code",
    "delivery_type_value",
    "state_code",
    "state_value",
    "state_child_state",
    "masked_state",
    "bosta_created_at",
    "bosta_updated_at",
    "pending_pickup_at",
    "collected_from_business_at",
    "picked_up_at",
    "delivery_time",
    "receiver_bosta_id",
    "receiver_name",
    "receiver_phone",
    "receiver_second_phone",
    "dropoff_country_code",
    "dropoff_country_name",
    "dropoff_city",
    "dropoff_zone",
    "dropoff_district",
    "dropoff_first_line",
    "dropoff_second_line",
    "dropoff_building_number",
    "dropoff_floor",
    "dropoff_apartment",
    "package_items_count",
    "package_description",
    "package_type",
    "package_size",
    "package_weight",
    "attempts_count",
    "delivery_attempts_count",
    "return_attempts_count",
    "pickup_attempts_count",
    "cod_amount",
    "original_cod_amount",
    "shipment_fees",
    "shipping_fee",
    "bundle_discount",
    "opening_package_fee",
    "bosta_material_fee",
    "price_before_vat",
    "price_after_vat",
    "vat_rate",
    "pricing_currency_code",
    "lifecycle_stage",
    "return_scenario",
    "lifecycle_rule_code",
    "lifecycle_ambiguous",
})

LIFECYCLE_VALUE_FIELDS = frozenset({
    "lifecycle_stage",
    "return_scenario",
    "lifecycle_rule_code",
    "lifecycle_ambiguous",
})

LIFECYCLE_SOURCE_FIELDS = frozenset({
    "delivery_type_code",
    "delivery_type_value",
    "state_code",
    "state_value",
    "state_child_state",
    "masked_state",
    "pending_pickup_at",
    "collected_from_business_at",
    "picked_up_at",
    "delivery_time",
    "bosta_updated_at",
})

ITEM_VALUE_FIELDS = frozenset({
    "sequence",
    "bosta_product_info_id",
    "external_product_id",
    "title",
    "quantity",
    "product_type",
    "options_string",
})

PROTECTED_EXTERNAL_FIELDS = frozenset({
    "company_id",
    "id",
    "create_uid",
    "write_uid",
    "create_date",
    "write_date",
})


class BostaPersistenceService:
    """Persist normalized deliveries in one authoritative company context."""

    def __init__(self, env):
        self.env = env
        self.Delivery = env["bosta.delivery"]
        self.Item = env["bosta.delivery.item"]
        self.lifecycle_interpreter = BostaLifecycleInterpreter()

    @staticmethod
    def empty_summary():
        return {
            "seen": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "conflicts": 0,
            "errors": 0,
        }

    def _company_delivery_model(self, company):
        if not company or company._name != "res.company" or len(company) != 1:
            raise BostaPersistenceDataError("A single target company is required for Bosta persistence.")
        if not self.env.su and company.id not in self.env.user.company_ids.ids:
            raise BostaPersistenceDataError("The target company is not available to the current user.")
        allowed_ids = list(self.env.context.get("allowed_company_ids") or self.env.user.company_ids.ids)
        if company.id not in allowed_ids:
            allowed_ids.append(company.id)
        return self.Delivery.with_context(allowed_company_ids=allowed_ids).with_company(company)

    def _sanitize_values(self, normalized):
        if not isinstance(normalized, dict):
            raise BostaPersistenceDataError()
        raw_values = normalized.get("values")
        if not isinstance(raw_values, dict):
            raise BostaPersistenceDataError()

        # Company/system metadata is never authoritative from API-derived data.
        values = {
            key: value
            for key, value in raw_values.items()
            if (
                key in DELIVERY_VALUE_FIELDS
                and key not in PROTECTED_EXTERNAL_FIELDS
                and key not in LIFECYCLE_VALUE_FIELDS
            )
        }
        for required in ("bosta_delivery_id", "tracking_number"):
            value = values.get(required)
            if not isinstance(value, str) or not value.strip():
                raise BostaPersistenceDataError("Normalized Bosta delivery identity is required.")
            values[required] = value.strip()

        return values

    @staticmethod
    def _same_value(current, incoming):
        # Odoo commonly represents SQL NULL as False in record cache.  Treat an
        # explicit normalized null/false/empty text as equivalent when the ORM
        # would persist the same empty value; presence still controls whether a
        # field is considered for update at all.
        if current in (False, None, "") and incoming in (False, None, ""):
            return True
        return current == incoming

    def _changed_values(self, record, incoming_values):
        changed = {}
        for field_name, incoming in incoming_values.items():
            if field_name not in record._fields:
                continue
            current = record[field_name]
            if not self._same_value(current, incoming):
                changed[field_name] = incoming
        return changed

    def _resolve_identity(self, model, company, values):
        incoming_id = values["bosta_delivery_id"]
        incoming_tracking = values["tracking_number"]
        domain_company = [("company_id", "=", company.id)]
        by_id = model.search(domain_company + [("bosta_delivery_id", "=", incoming_id)], limit=1)
        by_tracking = model.search(domain_company + [("tracking_number", "=", incoming_tracking)], limit=1)

        if by_id and by_tracking and by_id != by_tracking:
            raise BostaPersistenceIdentityConflict()
        return by_id or by_tracking

    @staticmethod
    def _is_stale(record, values):
        incoming = values.get("bosta_updated_at")
        stored = record.bosta_updated_at
        return bool(incoming and stored and incoming < stored)

    def _derive_lifecycle_values(self, normalized, *, record=None, incoming_values=None):
        """Interpret a patch against stored raw lifecycle facts without mutation."""
        raw_values = normalized.get("values") if isinstance(normalized, dict) else None
        raw_values = raw_values if isinstance(raw_values, dict) else {}

        merged_values = {}
        if record is not None:
            for field_name in LIFECYCLE_SOURCE_FIELDS:
                if field_name in record._fields:
                    merged_values[field_name] = record[field_name]

        # Preserve future interpreter-only fields such as an explicitly supplied
        # normalized flow_type while keeping persistence field filtering separate.
        for key, value in raw_values.items():
            if key in LIFECYCLE_SOURCE_FIELDS or key == "flow_type":
                merged_values[key] = value
        if incoming_values:
            for key, value in incoming_values.items():
                if key in LIFECYCLE_SOURCE_FIELDS:
                    merged_values[key] = value

        lifecycle_envelope = {
            "values": merged_values,
            "items": None,
            "timeline": normalized.get("timeline") if isinstance(normalized, dict) else None,
            "source_kind": normalized.get("source_kind") if isinstance(normalized, dict) else None,
        }
        return self.lifecycle_interpreter.interpret(lifecycle_envelope)

    @staticmethod
    def _protect_lifecycle_regression(record, values):
        """Keep stronger lifecycle evidence unless the payload is actually newer."""
        current_stage = record.lifecycle_stage
        incoming_stage = values.get("lifecycle_stage")
        if not current_stage or not incoming_stage or current_stage == incoming_stage:
            return values

        incoming_updated = values.get("bosta_updated_at")
        stored_updated = record.bosta_updated_at
        has_newer_evidence = bool(
            incoming_updated
            and (not stored_updated or incoming_updated > stored_updated)
        )
        if has_newer_evidence:
            return values

        strength = {
            "unknown": 10,
            "ambiguous": 15,
            "pre_pickup": 30,
            "with_bosta": 40,
            "customer_return_pickup": 50,
            "returning_to_origin": 60,
            "terminated": 70,
            "delivered_to_customer": 80,
            "customer_return_completed": 80,
            "returned_to_origin": 90,
            "lost": 100,
            "damaged": 100,
        }
        if strength.get(incoming_stage, 0) >= strength.get(current_stage, 0):
            return values

        protected = dict(values)
        for field_name in LIFECYCLE_VALUE_FIELDS:
            protected.pop(field_name, None)
        return protected

    @staticmethod
    def _item_key(values):
        bosta_product_info_id = values.get("bosta_product_info_id")
        if bosta_product_info_id not in (None, False, ""):
            return ("bosta_product_info_id", str(bosta_product_info_id))
        sequence = values.get("sequence", 10)
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise BostaPersistenceDataError("Normalized Bosta item sequence must be an integer.")
        external_product_id = values.get("external_product_id")
        if external_product_id in (None, False):
            external_product_id = ""
        return ("fallback", str(external_product_id), sequence)

    def _sanitize_item(self, item):
        if not isinstance(item, dict):
            raise BostaPersistenceDataError("Normalized Bosta items must be objects.")
        values = {key: value for key, value in item.items() if key in ITEM_VALUE_FIELDS}
        values.setdefault("sequence", 10)
        sequence = values["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise BostaPersistenceDataError("Normalized Bosta item sequence must be an integer.")
        quantity = values.get("quantity")
        if quantity is not None:
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                raise BostaPersistenceDataError("Normalized Bosta item quantity must be numeric.")
            if quantity < 0:
                raise BostaPersistenceDataError("Normalized Bosta item quantity cannot be negative.")
        return values

    def _sync_items(self, delivery, items):
        if items is None:
            return False
        if not isinstance(items, list):
            raise BostaPersistenceDataError("Normalized Bosta items must be a list or null.")

        # Snapshot only rows that existed before this reconciliation. Newly
        # created rows must never be mistaken for stale rows later in the same
        # pass, even if the One2many cache refreshes.
        Item = delivery.env["bosta.delivery.item"]
        existing_rows = delivery.item_ids
        existing_by_key = {}
        for existing in existing_rows.sorted(lambda item: (item.sequence, item.id)):
            key = self._item_key({
                "bosta_product_info_id": existing.bosta_product_info_id,
                "external_product_id": existing.external_product_id,
                "sequence": existing.sequence,
            })
            existing_by_key.setdefault(key, existing)

        changed = False
        matched_existing_ids = set()
        incoming_keys = set()
        for raw_item in items:
            values = self._sanitize_item(raw_item)
            key = self._item_key(values)
            if key in incoming_keys:
                raise BostaPersistenceDataError("Normalized Bosta item identities must be unique within a delivery.")
            incoming_keys.add(key)
            existing = existing_by_key.get(key)
            if existing and existing.id not in matched_existing_ids:
                matched_existing_ids.add(existing.id)
                updates = self._changed_values(existing, values)
                if updates:
                    existing.write(updates)
                    changed = True
            else:
                Item.create({"delivery_id": delivery.id, **values})
                changed = True

        stale_rows = existing_rows.filtered(lambda item: item.id not in matched_existing_ids)
        if stale_rows:
            stale_rows.unlink()
            changed = True
        return changed

    def upsert_normalized_delivery(self, normalized, company):
        """Create/update one normalized delivery atomically and idempotently."""
        values = self._sanitize_values(normalized)
        items = normalized.get("items", None)
        model = self._company_delivery_model(company)

        with self.env.cr.savepoint():
            record = self._resolve_identity(model, company, values)
            if not record:
                lifecycle = self._derive_lifecycle_values(
                    normalized,
                    incoming_values=values,
                )
                create_values = {
                    key: value
                    for key, value in {**values, **lifecycle}.items()
                    if key in model._fields
                }
                create_values["company_id"] = company.id
                record = model.create(create_values)
                self._sync_items(record, items)
                return {"record": record, "action": "created"}

            # Identity split-brain was already checked above.  Older payloads
            # are not allowed to move mutable state, aliases, or items back.
            if self._is_stale(record, values):
                return {"record": record, "action": "unchanged"}

            lifecycle = self._derive_lifecycle_values(
                normalized,
                record=record,
                incoming_values=values,
            )
            values = {**values, **lifecycle}
            values = self._protect_lifecycle_regression(record, values)
            updates = self._changed_values(record, values)
            parent_changed = bool(updates)
            if updates:
                record.write(updates)
            items_changed = self._sync_items(record, items)
            action = "updated" if parent_changed or items_changed else "unchanged"
            return {"record": record, "action": action}

    def persist_search_deliveries(
        self,
        extraction_service,
        company,
        *,
        page_size=None,
        max_pages=None,
        summary=None,
        post_persist=None,
    ):
        """Stream Search normalization into per-delivery persistence savepoints.

        ``post_persist`` runs in the same per-delivery savepoint. Phase 7/8 use
        this hook for atomic inventory and return evaluation after a delivery
        is safely persisted. Existing callers remain unchanged when the hook is
        absent.
        """
        summary = summary if summary is not None else self.empty_summary()
        iterator = extraction_service.iter_normalized_search_deliveries(
            page_size=page_size,
            max_pages=max_pages,
        )
        for normalized in iterator:
            summary["seen"] += 1
            try:
                with self.env.cr.savepoint():
                    result = self.upsert_normalized_delivery(normalized, company)
                    if post_persist:
                        post_persist(result["record"])
            except BostaPersistenceIdentityConflict:
                summary["conflicts"] += 1
                continue
            except BostaPersistenceDataError:
                summary["errors"] += 1
                continue
            summary[result["action"]] += 1
        return summary

    def enrich_delivery_from_details(self, extraction_service, delivery):
        delivery.ensure_one()
        normalized = extraction_service.get_normalized_delivery_details(
            delivery.tracking_number
        )
        return self.upsert_normalized_delivery(normalized, delivery.company_id)
