from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_inventory_service import BostaInventoryService
from .test_inventory_effects import Phase7InventoryMixin


class TestBostaInventoryIdempotency(Phase7InventoryMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock("Bosta P7 Idempotency")
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

    def test_68_70_repeated_picked_up_is_exactly_once(self):
        main, tester = self._pair("868")
        delivery = self._delivery("868")
        service = self._service()
        first = service.process_delivery(delivery)
        first_picking = first.outbound_picking_id
        second = service.process_delivery(delivery)
        self.assertEqual(second, first)
        self.assertEqual(second.outbound_picking_id, first_picking)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual(self.env["bosta.inventory.effect"].sudo().search_count([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ]), 1)

    def test_71_picked_up_to_delivered_does_not_repeat_source_deduction(self):
        main, tester = self._pair("871")
        delivery = self._delivery("871", stage="with_bosta")
        service = self._service()
        effect = service.process_delivery(delivery)
        outbound = effect.outbound_picking_id
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        delivery.write({"lifecycle_stage": "delivered_to_customer"})
        effect = service.process_delivery(delivery)
        self.assertEqual(effect.outbound_picking_id, outbound)
        self.assertTrue(effect.final_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_73_repeated_delivered_has_one_finalization(self):
        main, tester = self._pair("873")
        delivery = self._delivery("873", stage="delivered_to_customer")
        service = self._service()
        first = service.process_delivery(delivery)
        final = first.final_picking_id
        picking_count = self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%s/" % delivery.tracking_number)
        ])
        second = service.process_delivery(delivery)
        self.assertEqual(second.final_picking_id, final)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%s/" % delivery.tracking_number)
        ]), picking_count)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_74_first_observation_delivered_is_safe_and_idempotent(self):
        main, tester = self._pair("874")
        delivery = self._delivery("874", stage="delivered_to_customer")
        service = self._service()
        first = service.process_delivery(delivery)
        second = service.process_delivery(delivery)
        self.assertEqual(first.outbound_picking_id, second.outbound_picking_id)
        self.assertEqual(first.final_picking_id, second.final_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_91_applied_effect_keeps_resolved_product_snapshot(self):
        main_a = self._product("Snapshot A", "891", "main", False)
        main_b = self._product("Snapshot B", "OTHER891", "main", False)
        self._stock(main_a, 10); self._stock(main_b, 10)
        delivery = self._delivery("891")
        effect = self._service().process_delivery(delivery)
        mapping = effect.line_ids.mapping_id
        self.assertEqual(effect.line_ids.main_product_id, main_a)
        mapping.write({"odoo_product_id": main_b.id, "mapping_method": "manual", "mapping_status": "mapped"})
        self.assertEqual(effect.line_ids.main_product_id, main_a)
        self.assertEqual(effect.outbound_picking_id.move_ids.product_id, main_a)

    def test_92_later_mapping_edit_does_not_rewrite_moves_or_finalization(self):
        main_a = self._product("Move A", "892", "main", False)
        main_b = self._product("Move B", "OTHER892", "main", False)
        self._stock(main_a, 10); self._stock(main_b, 10)
        delivery = self._delivery("892", stage="with_bosta")
        service = self._service()
        effect = service.process_delivery(delivery)
        mapping = effect.line_ids.mapping_id
        mapping.write({"odoo_product_id": main_b.id, "mapping_method": "manual", "mapping_status": "mapped"})
        delivery.write({"lifecycle_stage": "delivered_to_customer"})
        effect = service.process_delivery(delivery)
        self.assertEqual(effect.final_picking_id.move_ids.product_id, main_a)
        self.assertEqual(self._qty(main_b), 10)

    def test_93_blocked_mapping_can_retry_after_manual_resolution(self):
        delivery = self._delivery("893")
        service = self._service()
        blocked = service.process_delivery(delivery)
        self.assertEqual(blocked.status, "blocked_mapping")
        product = self._product("Manual Resolution", "OTHER893", "main", False)
        self._stock(product, 10)
        mapping = self.env["bosta.product.mapping"].sudo().search([
            ("company_id", "=", self.company.id), ("source_product_code", "=", "893")
        ], limit=1)
        mapping.write({"mapping_status": "mapped", "mapping_method": "manual", "odoo_product_id": product.id})
        applied = service.process_delivery(delivery)
        self.assertEqual(applied.status, "outbound_applied")
        self.assertEqual(self._qty(product), 9)

    def test_94_95_retry_applies_once_then_noop(self):
        delivery = self._delivery("895")
        service = self._service()
        service.process_delivery(delivery)
        product = self._product("Retry Once", "OTHER895", "main", False)
        self._stock(product, 10)
        mapping = self.env["bosta.product.mapping"].sudo().search([
            ("company_id", "=", self.company.id), ("source_product_code", "=", "895")
        ], limit=1)
        mapping.write({"mapping_status": "mapped", "mapping_method": "manual", "odoo_product_id": product.id})
        first = service.process_delivery(delivery)
        first_picking = first.outbound_picking_id
        second = service.process_delivery(delivery)
        self.assertEqual(second.outbound_picking_id, first_picking)
        self.assertEqual(self._qty(product), 9)

    def test_97_unexpected_programming_error_is_not_swallowed(self):
        delivery = self._delivery("897")
        service = self._service()
        with patch.object(service.mapping, "resolve_delivery", side_effect=RuntimeError("synthetic-programming-error")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-programming-error"):
                service.process_delivery(delivery)

    def test_98_database_uniqueness_prevents_duplicate_effect(self):
        delivery = self._delivery("898", stage="pre_pickup", collected=False)
        effect = self._service().process_delivery(delivery)
        self.assertTrue(effect)
        Effect = self.env["bosta.inventory.effect"].sudo().with_context(bosta_inventory_engine=True)
        with self.env.cr.savepoint(), self.assertRaises(IntegrityError):
            Effect.create({
                "company_id": self.company.id,
                "delivery_id": delivery.id,
                "status": "pending_departure",
            })

    def _alternate_stock_context(self, suffix):
        warehouse = self.env["stock.warehouse"].sudo().with_company(self.company).create({
            "name": "Bosta P7 Alternate %s" % suffix,
            "code": ("A%s" % suffix)[-5:].upper(),
            "company_id": self.company.id,
        })
        transit = self.env["stock.location"].sudo().with_company(self.company).create({
            "name": "Bosta P7 Alternate Transit %s" % suffix,
            "usage": "transit",
            "company_id": self.company.id,
            "location_id": self.env.ref("stock.stock_location_locations").id,
        })
        return warehouse, warehouse.lot_stock_id, transit

    def test_99_applied_location_snapshot_survives_config_change_and_finalizes_from_original_transit(self):
        main, tester = self._pair("899")
        delivery = self._delivery("899", stage="with_bosta")
        service = self._service()
        effect = service.process_delivery(delivery)
        outbound = effect.outbound_picking_id
        self.assertEqual(effect.source_location_id, self.source)
        self.assertEqual(effect.transit_location_id, self.transit)
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (1, 1))

        warehouse_b, source_b, transit_b = self._alternate_stock_context("899")
        self.config.write({
            "stock_source_location_id": source_b.id,
            "bosta_transit_location_id": transit_b.id,
            "stock_picking_type_id": warehouse_b.int_type_id.id,
        })
        delivery.write({"lifecycle_stage": "delivered_to_customer"})

        finalized = service.process_delivery(delivery)
        self.assertEqual(finalized.outbound_picking_id, outbound)
        self.assertEqual(finalized.source_location_id, self.source)
        self.assertEqual(finalized.transit_location_id, self.transit)
        self.assertEqual(finalized.final_picking_id.location_id, self.transit)
        self.assertEqual(finalized.final_picking_id.picking_type_id, self.warehouse.out_type_id)
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (0, 0))
        self.assertEqual((self._qty(main, transit_b), self._qty(tester, transit_b)), (0, 0))
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual((self._qty(main, source_b), self._qty(tester, source_b)), (0, 0))

        picking_count = self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%s/" % delivery.tracking_number)
        ])
        repeated = service.process_delivery(delivery)
        self.assertEqual(repeated.final_picking_id, finalized.final_picking_id)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%s/" % delivery.tracking_number)
        ]), picking_count)

    def test_100_blocked_effect_without_outbound_may_adopt_new_config_locations(self):
        main, tester = self._pair("8100", stock=0)
        delivery = self._delivery("8100")
        service = self._service()
        blocked = service.process_delivery(delivery)
        self.assertEqual(blocked.status, "blocked_stock")
        self.assertFalse(blocked.outbound_picking_id)
        self.assertEqual(blocked.source_location_id, self.source)
        self.assertEqual(blocked.transit_location_id, self.transit)

        warehouse_b, source_b, transit_b = self._alternate_stock_context("100")
        self._stock(main, 5, source_b)
        self._stock(tester, 5, source_b)
        self.config.write({
            "stock_source_location_id": source_b.id,
            "bosta_transit_location_id": transit_b.id,
            "stock_picking_type_id": warehouse_b.int_type_id.id,
        })

        applied = service.process_delivery(delivery)
        self.assertEqual(applied.status, "outbound_applied")
        self.assertEqual(applied.source_location_id, source_b)
        self.assertEqual(applied.transit_location_id, transit_b)
        self.assertEqual(applied.outbound_picking_id.location_id, source_b)
        self.assertEqual(applied.outbound_picking_id.location_dest_id, transit_b)
        self.assertEqual(applied.outbound_picking_id.picking_type_id, warehouse_b.int_type_id)
        self.assertEqual((self._qty(main, source_b), self._qty(tester, source_b)), (4, 4))
        self.assertEqual((self._qty(main, transit_b), self._qty(tester, transit_b)), (1, 1))

    def test_101_applied_effect_locations_are_immutable_audit_snapshots(self):
        self._pair("8101")
        effect = self._service().process_delivery(self._delivery("8101"))
        _warehouse_b, source_b, transit_b = self._alternate_stock_context("101")
        protected = effect.sudo().with_context(bosta_inventory_engine=True)
        with self.assertRaises(ValidationError):
            protected.write({"source_location_id": source_b.id})
        with self.assertRaises(ValidationError):
            protected.write({"transit_location_id": transit_b.id})
        self.assertEqual(effect.source_location_id, self.source)
        self.assertEqual(effect.transit_location_id, self.transit)

    def test_102_pending_effect_without_outbound_adopts_new_config_when_it_later_departs(self):
        main, tester = self._pair("8102", stock=0)
        delivery = self._delivery("8102", stage="pre_pickup", collected=False)
        service = self._service()
        pending = service.process_delivery(delivery)
        self.assertEqual(pending.status, "pending_departure")
        self.assertFalse(pending.outbound_picking_id)

        warehouse_b, source_b, transit_b = self._alternate_stock_context("102")
        self._stock(main, 5, source_b)
        self._stock(tester, 5, source_b)
        self.config.write({
            "stock_source_location_id": source_b.id,
            "bosta_transit_location_id": transit_b.id,
            "stock_picking_type_id": warehouse_b.int_type_id.id,
        })
        departure = self.cutoff + timedelta(days=3)
        delivery.write({
            "lifecycle_stage": "with_bosta",
            "collected_from_business_at": departure,
        })

        applied = service.process_delivery(delivery)
        self.assertEqual(applied.status, "outbound_applied")
        self.assertEqual(applied.source_location_id, source_b)
        self.assertEqual(applied.transit_location_id, transit_b)
        self.assertEqual(applied.outbound_picking_id.location_id, source_b)
        self.assertEqual(applied.outbound_picking_id.location_dest_id, transit_b)
