from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase

from ..services.exceptions import BostaApiConnectionError
from .test_inventory_effects import Phase7InventoryMixin


class TestPhase8SyncIntegration(Phase7InventoryMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock("Bosta P8 Sync")
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase8-sync-key"}, clear=False):
            cls.config = cls.env["bosta.integration.config"].sudo().with_company(cls.company).create({
                "company_id": cls.company.id,
                "integration_enabled": True,
                "inventory_sync_enabled": True,
                "inventory_effective_from": cls.cutoff,
                "stock_source_location_id": cls.source.id,
                "bosta_transit_location_id": cls.transit.id,
                "stock_picking_type_id": cls.warehouse.int_type_id.id,
                "page_size": 200,
                "max_pages": 10,
            })
        cls.counter = 0

    @classmethod
    def _forward_payload(cls, code, *, delivery_id=None, tracking=None):
        cls.counter += 1
        suffix = cls.counter
        return {
            "values": {
                "bosta_delivery_id": delivery_id or "P8-FWD-%s-%s" % (code, suffix),
                "tracking_number": tracking or "T-P8-FWD-%s-%s" % (code, suffix),
                "creation_source": "SHOPIFY",
                "delivery_type_code": 10,
                "delivery_type_value": "Send",
                "state_value": "Picked Up",
                "bosta_created_at": cls.cutoff + timedelta(days=2),
                "bosta_updated_at": cls.cutoff + timedelta(days=2, hours=3),
                "collected_from_business_at": cls.cutoff + timedelta(days=2, hours=2),
                "package_description": "Product %s x 1 (088.01-%s.050)" % (code, code),
            },
            "items": None,
            "timeline": None,
            "source_kind": "search",
        }

    @classmethod
    def _rto_payload(cls, delivery_id, tracking):
        return {
            "values": {
                "bosta_delivery_id": delivery_id,
                "tracking_number": tracking,
                "creation_source": "SHOPIFY",
                "delivery_type_code": 20,
                "delivery_type_value": "Return to Origin",
                "state_value": "Delivered",
                "state_code": 46,
                "bosta_created_at": cls.cutoff + timedelta(days=3),
                "bosta_updated_at": cls.cutoff + timedelta(days=4),
            },
            "items": None,
            "timeline": None,
            "source_kind": "search",
        }

    def _run_sync(self, config, payloads):
        def iterator(**_kwargs):
            return iter(payloads)

        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase8-sync-key"}, clear=False), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaExtractionService") as extraction_class:
            extraction_class.return_value.iter_normalized_search_deliveries.side_effect = iterator
            return config.sudo().action_sync_bosta_deliveries()

    def _sync_forward(self, code, stock=5):
        product = self._product("P8 Sync " + code, code, "main", False)
        self._stock(product, stock)
        payload = self._forward_payload(code)
        self._run_sync(self.config, [payload])
        delivery = self.env["bosta.delivery"].sudo().search([
            ("company_id", "=", self.company.id),
            ("bosta_delivery_id", "=", payload["values"]["bosta_delivery_id"]),
        ], limit=1)
        return product, delivery

    def _precreate_linked_rto(self, original, delivery_id, tracking):
        return self.env["bosta.delivery"].sudo().with_company(self.company).create({
            "company_id": self.company.id,
            "bosta_delivery_id": delivery_id,
            "tracking_number": tracking,
            "creation_source": "SHOPIFY",
            "delivery_type_code": 20,
            "delivery_type_value": "Return to Origin",
            "lifecycle_stage": "returning_to_origin",
            "return_scenario": "pre_delivery_return",
            "bosta_created_at": self.cutoff + timedelta(days=3),
            "original_delivery_id": original.id,
        })

    def test_46_phase5_sync_plus_completed_rto_restores_exactly_once(self):
        product, original = self._sync_forward("S846")
        self.assertEqual(self._qty(product), 4)
        rto = self._precreate_linked_rto(original, "P8-RTO-846", "T-P8-RTO-846")
        self._run_sync(self.config, [self._rto_payload(rto.bosta_delivery_id, rto.tracking_number)])
        case = self.env["bosta.return.case"].sudo().search([("return_delivery_id", "=", rto.id)], limit=1)
        self.assertEqual(case.state, "restored")
        self.assertEqual(self._qty(product), 5)
        self.assertTrue(case.restoration_picking_id)

    def test_47_repeated_same_sync_creates_no_duplicate_stock(self):
        product, original = self._sync_forward("S847")
        rto = self._precreate_linked_rto(original, "P8-RTO-847", "T-P8-RTO-847")
        payload = self._rto_payload(rto.bosta_delivery_id, rto.tracking_number)
        self._run_sync(self.config, [payload])
        case = self.env["bosta.return.case"].sudo().search([("return_delivery_id", "=", rto.id)], limit=1)
        picking = case.restoration_picking_id
        self._run_sync(self.config, [payload])
        case.invalidate_recordset()
        self.assertEqual(case.restoration_picking_id, picking)
        self.assertEqual(self._qty(product), 5)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([("origin", "=", picking.origin)]), 1)

    def test_48_busy_same_config_return_retry_does_not_enter_return_engine(self):
        with patch.object(type(self.config), "_try_acquire_sync_lock", return_value=False), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaReturnService") as return_class, \
             self.assertRaises(UserError):
            self.config.sudo().action_process_pending_returns()
        return_class.assert_not_called()

    def test_49_different_company_configs_restore_independently(self):
        product_a, original_a = self._sync_forward("S849A")
        rto_a = self._precreate_linked_rto(original_a, "P8-RTO-849A", "T-P8-RTO-849A")

        company_b, warehouse_b, source_b, transit_b = self._make_company_stock("Bosta P8 Sync B")
        tmpl_b = self.env["product.template"].sudo().with_company(company_b).create({
            "name": "P8 Sync B", "default_code": "S849B", "type": "consu",
            "is_storable": True, "tracking": "none", "company_id": company_b.id,
        })
        product_b = tmpl_b.product_variant_id
        product_b.write({"bosta_product_role": "main", "bosta_tester_required": False})
        self.env["stock.quant"].sudo().with_company(company_b)._update_available_quantity(product_b, source_b, 7)
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase8-sync-key"}, clear=False):
            config_b = self.env["bosta.integration.config"].sudo().with_company(company_b).create({
                "company_id": company_b.id, "integration_enabled": True, "inventory_sync_enabled": True,
                "inventory_effective_from": self.cutoff, "stock_source_location_id": source_b.id,
                "bosta_transit_location_id": transit_b.id, "stock_picking_type_id": warehouse_b.int_type_id.id,
            })
        forward_b = self._forward_payload("S849B", delivery_id="P8-FWD-849B", tracking="T-P8-FWD-849B")
        self._run_sync(config_b, [forward_b])
        original_b = self.env["bosta.delivery"].sudo().with_company(company_b).search([
            ("company_id", "=", company_b.id), ("bosta_delivery_id", "=", "P8-FWD-849B")
        ], limit=1)
        rto_b = self.env["bosta.delivery"].sudo().with_company(company_b).create({
            "company_id": company_b.id, "bosta_delivery_id": "P8-RTO-849B", "tracking_number": "T-P8-RTO-849B",
            "delivery_type_code": 20, "delivery_type_value": "Return to Origin",
            "lifecycle_stage": "returning_to_origin", "return_scenario": "pre_delivery_return",
            "bosta_created_at": self.cutoff + timedelta(days=3), "original_delivery_id": original_b.id,
        })
        self._run_sync(self.config, [self._rto_payload(rto_a.bosta_delivery_id, rto_a.tracking_number)])
        self._run_sync(config_b, [self._rto_payload(rto_b.bosta_delivery_id, rto_b.tracking_number)])
        qty_a = product_a.sudo().with_company(self.company).with_context(location=self.source.id).qty_available
        qty_b = product_b.sudo().with_company(company_b).with_context(location=source_b.id).qty_available
        self.assertEqual(qty_a, 5)
        self.assertEqual(qty_b, 7)

    def test_50_api_failure_creates_no_fake_restoration(self):
        product, original = self._sync_forward("S850")
        rto = self._precreate_linked_rto(original, "P8-RTO-850", "T-P8-RTO-850")
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase8-sync-key"}, clear=False), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaExtractionService") as extraction_class:
            extraction_class.return_value.iter_normalized_search_deliveries.side_effect = BostaApiConnectionError()
            self.config.sudo().action_sync_bosta_deliveries()
        case = self.env["bosta.return.case"].sudo().search([("return_delivery_id", "=", rto.id)], limit=1)
        self.assertFalse(case)
        self.assertEqual(self._qty(product), 4)

    def test_blocked_return_in_sync_does_not_stop_following_valid_return(self):
        missing_effect_original = self.env["bosta.delivery"].sudo().with_company(self.company).create({
            "company_id": self.company.id, "bosta_delivery_id": "P8-FWD-BLOCKED", "tracking_number": "T-P8-FWD-BLOCKED",
            "delivery_type_code": 10, "delivery_type_value": "Send", "lifecycle_stage": "with_bosta",
        })
        blocked_rto = self._precreate_linked_rto(missing_effect_original, "P8-RTO-BLOCKED", "T-P8-RTO-BLOCKED")
        product, valid_original = self._sync_forward("S851")
        valid_rto = self._precreate_linked_rto(valid_original, "P8-RTO-VALID", "T-P8-RTO-VALID")
        self._run_sync(self.config, [
            self._rto_payload(blocked_rto.bosta_delivery_id, blocked_rto.tracking_number),
            self._rto_payload(valid_rto.bosta_delivery_id, valid_rto.tracking_number),
        ])
        blocked_case = self.env["bosta.return.case"].sudo().search([("return_delivery_id", "=", blocked_rto.id)], limit=1)
        valid_case = self.env["bosta.return.case"].sudo().search([("return_delivery_id", "=", valid_rto.id)], limit=1)
        self.assertEqual(blocked_case.state, "blocked")
        self.assertEqual(valid_case.state, "restored")
        self.assertEqual(self._qty(product), 5)
