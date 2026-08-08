from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_persistence_service import BostaPersistenceService


class TestBostaDeliveryModels(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env["bosta.delivery"]
        cls.Item = cls.env["bosta.delivery.item"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Delivery Models A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Delivery Models B"})
        cls._counter = 0

    @classmethod
    def _values(cls, company=None, **extra):
        cls._counter += 1
        values = {
            "company_id": (company or cls.company_a).id,
            "bosta_delivery_id": f"delivery-{cls._counter}",
            "tracking_number": f"tracking-{cls._counter}",
        }
        values.update(extra)
        return values

    def test_models_are_registered(self):
        self.assertEqual(self.Delivery._name, "bosta.delivery")
        self.assertEqual(self.Item._name, "bosta.delivery.item")

    def test_required_delivery_id_and_tracking_number(self):
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Delivery.create({
                "company_id": self.company_a.id,
                "tracking_number": "required-tracking",
            })
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Delivery.create({
                "company_id": self.company_a.id,
                "bosta_delivery_id": "required-id",
            })

    def test_delivery_id_is_unique_per_company(self):
        self.Delivery.create(self._values(bosta_delivery_id="same-id"))
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Delivery.create(self._values(bosta_delivery_id="same-id"))

        other = self.Delivery.create(
            self._values(company=self.company_b, bosta_delivery_id="same-id")
        )
        self.assertTrue(other.exists())

    def test_tracking_number_is_unique_per_company(self):
        self.Delivery.create(self._values(tracking_number="same-tracking"))
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Delivery.create(self._values(tracking_number="same-tracking"))

        other = self.Delivery.create(
            self._values(company=self.company_b, tracking_number="same-tracking")
        )
        self.assertTrue(other.exists())

    def test_duplicate_business_reference_is_allowed_for_forward_and_return(self):
        reference = "h0wiaf-nn:#1067"
        forward = self.Delivery.create(
            self._values(
                business_reference=reference,
                delivery_type_code=10,
                delivery_type_value="Send",
            )
        )
        customer_return = self.Delivery.create(
            self._values(
                business_reference=reference,
                delivery_type_code=25,
                delivery_type_value="Customer Return Pickup",
            )
        )
        self.assertEqual(forward.business_reference, customer_return.business_reference)
        self.assertEqual(forward.flow_type, "forward")
        self.assertEqual(customer_return.flow_type, "customer_return")

    def test_duplicate_unique_business_reference_is_allowed(self):
        value = "shared-unique-business-reference"
        first = self.Delivery.create(self._values(unique_business_reference=value))
        second = self.Delivery.create(self._values(unique_business_reference=value))
        self.assertNotEqual(first.id, second.id)

    def test_flow_classification_from_codes(self):
        cases = (
            (10, "forward"),
            (20, "return_to_origin"),
            (25, "customer_return"),
            (999, "other"),
        )
        for code, expected in cases:
            with self.subTest(code=code):
                delivery = self.Delivery.create(self._values(delivery_type_code=code))
                self.assertEqual(delivery.flow_type, expected)

    def test_flow_classification_from_values(self):
        cases = (
            ("Send", "forward"),
            ("Return to Origin", "return_to_origin"),
            ("Customer Return Pickup", "customer_return"),
            ("Future Delivery Type", "other"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                delivery = self.Delivery.create(self._values(delivery_type_value=value))
                self.assertEqual(delivery.flow_type, expected)

    def test_unknown_future_type_does_not_raise(self):
        delivery = self.Delivery.create(
            self._values(delivery_type_code=812, delivery_type_value="Teleport")
        )
        self.assertEqual(delivery.flow_type, "other")

    def test_raw_state_is_independent_of_flow(self):
        normal = self.Delivery.create(
            self._values(
                delivery_type_code=10,
                state_code=45,
                state_value="Delivered",
            )
        )
        customer_return = self.Delivery.create(
            self._values(
                delivery_type_code=25,
                state_code=46,
                state_value="Delivered",
            )
        )
        self.assertEqual(normal.state_value, customer_return.state_value)
        self.assertNotEqual(normal.flow_type, customer_return.flow_type)

    def test_optional_api_data_can_be_missing(self):
        delivery = self.Delivery.create(self._values(delivery_type_code=25))
        self.assertFalse(delivery.item_ids)
        self.assertFalse(delivery.unique_business_reference)
        self.assertFalse(delivery.shopify_order_id)
        self.assertFalse(delivery.collected_from_business_at)
        self.assertEqual(delivery.flow_type, "customer_return")

    def test_pricing_and_original_cod_are_independent_storage(self):
        values = self._values(cod_amount=0.0, original_cod_amount=850.0)
        result = BostaPersistenceService(self.env).upsert_normalized_delivery(
            {
                "values": values,
                "items": None,
                "timeline": None,
                "source_kind": "search",
            },
            self.company_a,
        )
        delivery = result["record"]
        self.assertEqual(delivery.cod_amount, 0.0)
        self.assertEqual(delivery.original_cod_amount, 850.0)

    def test_source_agnostic_creation_source(self):
        for source in ("SHOPIFY", "WOOCOMMERCE", "BUSINESS_DASHBOARD", "FUTURE_SOURCE"):
            with self.subTest(source=source):
                delivery = self.Delivery.create(self._values(creation_source=source))
                self.assertEqual(delivery.creation_source, source)

    def test_delivery_item_and_multiple_items(self):
        delivery = self.Delivery.create(self._values())
        first = self.Item.create({
            "delivery_id": delivery.id,
            "external_product_id": "product-1",
            "title": "Bottle",
            "quantity": 1,
        })
        second = self.Item.create({
            "delivery_id": delivery.id,
            "external_product_id": "product-2",
            "title": "Tester",
            "quantity": 2,
        })
        self.assertEqual(first.company_id, delivery.company_id)
        self.assertEqual(delivery.item_count, 2)
        self.assertEqual(delivery.item_ids, first | second)

    def test_negative_item_quantity_is_rejected(self):
        delivery = self.Delivery.create(self._values())
        with self.assertRaises(ValidationError):
            self.Item.create({"delivery_id": delivery.id, "quantity": -0.01})

    def test_zero_item_quantity_is_allowed(self):
        delivery = self.Delivery.create(self._values())
        item = self.Item.create({"delivery_id": delivery.id, "quantity": 0})
        self.assertEqual(item.quantity, 0)

    def test_deleting_delivery_cascades_items(self):
        delivery = self.Delivery.create(self._values())
        item = self.Item.create({"delivery_id": delivery.id, "quantity": 1})
        item_id = item.id
        delivery.unlink()
        self.assertFalse(self.Item.browse(item_id).exists())

    def test_return_can_reference_original_delivery(self):
        original = self.Delivery.create(self._values(delivery_type_code=10))
        returned = self.Delivery.create(
            self._values(delivery_type_code=25, original_delivery_id=original.id)
        )
        self.assertEqual(returned.original_delivery_id, original)
        self.assertIn(returned, original.return_delivery_ids)

    def test_self_reference_is_rejected(self):
        delivery = self.Delivery.create(self._values())
        with self.assertRaises(ValidationError):
            delivery.write({"original_delivery_id": delivery.id})

    def test_cross_company_original_delivery_is_rejected(self):
        original = self.Delivery.create(self._values(company=self.company_a))
        returned = self.Delivery.create(self._values(company=self.company_b))
        with self.assertRaises(UserError):
            returned.write({"original_delivery_id": original.id})

    def test_changing_company_cannot_break_existing_return_relation(self):
        original = self.Delivery.create(self._values(company=self.company_a))
        returned = self.Delivery.create(
            self._values(company=self.company_a, original_delivery_id=original.id)
        )
        self.assertEqual(returned.original_delivery_id, original)
        with self.assertRaises(UserError):
            original.write({"company_id": self.company_b.id})

    def test_deleting_original_sets_return_relation_to_null(self):
        original = self.Delivery.create(self._values())
        returned = self.Delivery.create(
            self._values(original_delivery_id=original.id, delivery_type_code=25)
        )
        original.unlink()
        self.assertFalse(returned.exists().original_delivery_id)
