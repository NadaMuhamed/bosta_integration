from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_inventory_service import BostaInventoryService


class Phase7InventoryMixin:
    cutoff = datetime(2026, 8, 1, 0, 0, 0)

    @classmethod
    def _make_company_stock(cls, label):
        company = cls.env["res.company"].create({"name": label})
        warehouse = cls.env["stock.warehouse"].sudo().search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            warehouse = cls.env["stock.warehouse"].sudo().create({
                "name": label + " Warehouse", "code": (label.replace(" ", "")[:5] or "P7WH").upper(),
                "company_id": company.id,
            })
        transit = cls.env["stock.location"].sudo().with_company(company).create({
            "name": label + " Bosta Transit",
            "usage": "transit",
            "company_id": company.id,
            "location_id": cls.env.ref("stock.stock_location_locations").id,
        })
        return company, warehouse, warehouse.lot_stock_id, transit

    def _product(self, name, code, role="main", tester_required=False, company=None):
        company = company or self.company
        tmpl = self.env["product.template"].sudo().with_company(company).create({
            "name": name,
            "default_code": code,
            "type": "consu",
            "is_storable": True,
            "tracking": "none",
            "company_id": company.id,
        })
        product = tmpl.product_variant_id
        product.write({
            "bosta_product_role": role,
            "bosta_tester_required": tester_required,
        })
        return product

    def _pair(self, code, stock=10):
        main = self._product("MAIN " + code, code, "main", True)
        tester = self._product("MAIN %s 3 ML" % code, code, "tester", False)
        main.write({"bosta_tester_product_id": tester.id})
        if stock:
            self._stock(main, stock)
            self._stock(tester, stock)
        return main, tester

    def _stock(self, product, quantity, location=None):
        location = location or self.source
        self.env["stock.quant"].sudo().with_company(self.company)._update_available_quantity(
            product, location, quantity
        )

    def _qty(self, product, location=None):
        location = location or self.source
        return product.sudo().with_company(self.company).with_context(location=location.id).qty_available

    def _delivery(self, code="701", qty=1, stage="with_bosta", collected=True, created=None, flow="forward", description=None, **extra):
        created = created or self.cutoff + timedelta(days=2)
        type_code, type_value = {
            "forward": (10, "Send"),
            "rto": (20, "Return to Origin"),
            "customer_return": (25, "Customer Return Pickup"),
        }[flow]
        vals = {
            "company_id": self.company.id,
            "bosta_delivery_id": "p7-%s-%s-%s" % (code, stage, self.env["bosta.delivery"].search_count([]) + 1),
            "tracking_number": "T-P7-%s-%s" % (code, self.env["bosta.delivery"].search_count([]) + 1),
            "creation_source": "SHOPIFY",
            "delivery_type_code": type_code,
            "delivery_type_value": type_value,
            "lifecycle_stage": stage,
            "bosta_created_at": created,
            "package_description": description or "Product %s x %s (088.01-%s.050)" % (code, qty, code),
            "cod_amount": 0,
        }
        if collected:
            vals["collected_from_business_at"] = created + timedelta(hours=2)
        vals.update(extra)
        return self.env["bosta.delivery"].sudo().with_context(bosta_delivery_persistence=True).with_company(self.company).create(vals)

    def _service(self):
        return BostaInventoryService(self.env, self.config)


class TestBostaInventoryEffects(Phase7InventoryMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock("Bosta P7 Inventory")
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase7-key"}, clear=False):
            cls.config = cls.env["bosta.integration.config"].sudo().with_company(cls.company).create({
                "company_id": cls.company.id,
                "integration_enabled": True,
                "inventory_sync_enabled": True,
                "inventory_effective_from": cls.cutoff,
                "stock_source_location_id": cls.source.id,
                "bosta_transit_location_id": cls.transit.id,
                "stock_picking_type_id": cls.warehouse.int_type_id.id,
            })

    def test_48_inventory_defaults_disabled(self):
        company, _wh, _source, _transit = self._make_company_stock("Bosta P7 Disabled")
        config = self.env["bosta.integration.config"].sudo().with_company(company).create({"company_id": company.id})
        self.assertFalse(config.inventory_sync_enabled)

    def test_49_disabled_inventory_zero_stock_mutation(self):
        main, tester = self._pair("749")
        delivery = self._delivery("749")
        before = (self._qty(main), self._qty(tester))
        self.config.write({"inventory_sync_enabled": False})
        self.assertFalse(self._service().process_delivery(delivery))
        self.assertEqual((self._qty(main), self._qty(tester)), before)

    def test_50_enable_requires_effective_from(self):
        company, wh, source, transit = self._make_company_stock("Bosta P7 Cutoff Required")
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase7-key"}, clear=False), self.assertRaises(ValidationError):
            self.env["bosta.integration.config"].sudo().with_company(company).create({
                "company_id": company.id, "integration_enabled": True,
                "inventory_sync_enabled": True,
                "stock_source_location_id": source.id,
                "bosta_transit_location_id": transit.id,
                "stock_picking_type_id": wh.int_type_id.id,
            })

    def test_51_enable_requires_internal_source(self):
        customer = self.env.ref("stock.stock_location_customers")
        with self.assertRaises(ValidationError):
            self.config.write({"stock_source_location_id": customer.id})

    def test_52_enable_requires_transit_location(self):
        with self.assertRaises(ValidationError):
            self.config.write({"bosta_transit_location_id": self.source.id})

    def test_53_historical_before_cutoff_zero_mutation(self):
        main, tester = self._pair("753")
        delivery = self._delivery("753", created=self.cutoff - timedelta(days=2))
        effect = self._service().process_delivery(delivery)
        self.assertEqual(effect.status, "not_applicable")
        self.assertEqual(self._qty(main), 10)
        self.assertEqual(self._qty(tester), 10)

    def test_55_pre_pickup_no_stock_move(self):
        main, tester = self._pair("755")
        delivery = self._delivery("755", stage="pre_pickup", collected=False)
        effect = self._service().process_delivery(delivery)
        self.assertEqual(effect.status, "pending_departure")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_56_terminated_before_pickup_no_move(self):
        main, tester = self._pair("756")
        delivery = self._delivery("756", stage="terminated", collected=False)
        effect = self._service().process_delivery(delivery)
        self.assertEqual(effect.status, "pending_departure")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_57_58_picked_up_moves_main_and_tester_to_transit(self):
        main, tester = self._pair("758")
        delivery = self._delivery("758")
        effect = self._service().process_delivery(delivery)
        self.assertEqual(effect.status, "outbound_applied")
        self.assertEqual(self._qty(main), 9)
        self.assertEqual(self._qty(tester), 9)
        self.assertEqual(self._qty(main, self.transit), 1)
        self.assertEqual(self._qty(tester, self.transit), 1)

    def test_59_quantity_two_moves_two_each(self):
        main, tester = self._pair("759")
        delivery = self._delivery("759", qty=2)
        self._service().process_delivery(delivery)
        self.assertEqual((self._qty(main), self._qty(tester)), (8, 8))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (2, 2))

    def test_60_required_missing_tester_blocks_whole_effect(self):
        main = self._product("P7 MISSING TESTER", "760", "main", True)
        self._stock(main, 10)
        effect = self._service().process_delivery(self._delivery("760"))
        self.assertEqual(effect.status, "blocked_tester")
        self.assertEqual(self._qty(main), 10)
        self.assertFalse(effect.outbound_picking_id)

    def test_61_explicit_tester_not_required_allows_main_only(self):
        main = self._product("P7 NO TESTER REQUIRED", "761", "main", False)
        self._stock(main, 10)
        effect = self._service().process_delivery(self._delivery("761"))
        self.assertEqual(effect.status, "outbound_applied")
        self.assertEqual(self._qty(main), 9)
        self.assertFalse(effect.line_ids.tester_product_id)

    def test_62_unmatched_line_blocks_entire_delivery(self):
        main, tester = self._pair("762")
        description = "Mapped x 1 (088.01-762.050)\nUnknown x 1 (088.01-999762.050)"
        effect = self._service().process_delivery(self._delivery("762", description=description))
        self.assertEqual(effect.status, "blocked_mapping")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))
        self.assertFalse(effect.outbound_picking_id)

    def test_63_conflicting_line_blocks_entire_delivery(self):
        main, tester = self._pair("763")
        self._product("P7 CONFLICT 1", "C763", "main")
        self._product("P7 CONFLICT 2", "C763", "main")
        description = "Mapped x 1 (088.01-763.050)\nConflict x 1 (088.01-C763.050)"
        effect = self._service().process_delivery(self._delivery("763", description=description))
        self.assertEqual(effect.status, "blocked_mapping")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_65_insufficient_main_stock_blocks(self):
        main, tester = self._pair("765", stock=0)
        self._stock(main, 1); self._stock(tester, 10)
        effect = self._service().process_delivery(self._delivery("765", qty=2))
        self.assertEqual(effect.status, "blocked_stock")
        self.assertEqual((self._qty(main), self._qty(tester)), (1, 10))
        self.assertFalse(effect.outbound_picking_id)

    def test_66_insufficient_tester_stock_blocks(self):
        main, tester = self._pair("766", stock=0)
        self._stock(main, 10); self._stock(tester, 1)
        effect = self._service().process_delivery(self._delivery("766", qty=2))
        self.assertEqual(effect.status, "blocked_stock")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 1))
        self.assertFalse(effect.outbound_picking_id)

    def test_72_delivered_finalizes_transit_to_customer(self):
        main, tester = self._pair("772")
        delivery = self._delivery("772", stage="delivered_to_customer")
        effect = self._service().process_delivery(delivery)
        self.assertEqual(effect.status, "delivered_finalized")
        self.assertTrue(effect.outbound_picking_id)
        self.assertTrue(effect.final_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (0, 0))

    def test_75_returning_to_origin_stays_in_transit(self):
        main, tester = self._pair("775")
        effect = self._service().process_delivery(self._delivery("775", stage="returning_to_origin"))
        self.assertEqual(effect.status, "outbound_applied")
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (1, 1))
        self.assertFalse(effect.final_picking_id)

    def test_76_returned_to_origin_does_not_restore(self):
        main, tester = self._pair("776")
        effect = self._service().process_delivery(self._delivery("776", stage="returned_to_origin"))
        self.assertEqual(effect.status, "outbound_applied")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (1, 1))

    def test_77_terminated_after_pickup_is_review_and_not_restored(self):
        main, tester = self._pair("777")
        effect = self._service().process_delivery(self._delivery("777", stage="terminated", collected=True))
        self.assertEqual(effect.status, "exception")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (1, 1))

    def test_78_79_lost_or_damaged_after_departure_not_restored(self):
        for stage, code in (("lost", "778"), ("damaged", "779")):
            main, tester = self._pair(code)
            effect = self._service().process_delivery(self._delivery(code, stage=stage, collected=True))
            self.assertEqual(effect.status, "exception")
            self.assertEqual(self._qty(main), 9)
            self.assertEqual(self._qty(tester), 9)

    def test_80_rto_record_no_new_outbound_deduction(self):
        main, tester = self._pair("780")
        effect = self._service().process_delivery(self._delivery("780", flow="rto", stage="returned_to_origin"))
        self.assertEqual(effect.status, "not_applicable")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_81_customer_return_record_no_outbound_deduction(self):
        main, tester = self._pair("781")
        effect = self._service().process_delivery(self._delivery("781", flow="customer_return", stage="customer_return_pickup"))
        self.assertEqual(effect.status, "not_applicable")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_82_customer_return_completed_no_restoration(self):
        main, tester = self._pair("782")
        effect = self._service().process_delivery(self._delivery("782", flow="customer_return", stage="customer_return_completed"))
        self.assertEqual(effect.status, "not_applicable")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_84_87_financial_fields_do_not_change_quantity_rule(self):
        main, tester = self._pair("784")
        delivery = self._delivery(
            "784", qty=2, cod_amount=1500, shipment_fees=7, shipping_fee=83,
            price_before_vat=100, price_after_vat=114,
        )
        self._service().process_delivery(delivery)
        self.assertEqual((self._qty(main), self._qty(tester)), (8, 8))

    def test_88_business_reference_not_used_as_product_identity(self):
        main, tester = self._pair("788")
        delivery = self._delivery("788", business_reference="999999")
        self._service().process_delivery(delivery)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_90_stock_transfer_origin_is_safe_bosta_identifier(self):
        self._pair("790")
        delivery = self._delivery("790", receiver_name="PII MUST NOT APPEAR", receiver_phone="01000000000")
        effect = self._service().process_delivery(delivery)
        origin = effect.outbound_picking_id.origin
        self.assertIn(delivery.tracking_number, origin)
        self.assertNotIn("PII MUST NOT APPEAR", origin)
        self.assertNotIn("01000000000", origin)
