from datetime import timedelta
from unittest.mock import patch

from odoo import api
from odoo.exceptions import UserError
from odoo.tests import TransactionCase

from .test_inventory_effects import Phase7InventoryMixin


class TestPhase7SyncInventoryIntegration(Phase7InventoryMixin, TransactionCase):
    """Exercise Phase 7 through the real Phase 5 persistence/sync callback path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock(
            "Bosta P7 Sync Integration"
        )
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase7-sync-key"}, clear=False):
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
    def _normalized(cls, code, *, tracking=None, external_id=None):
        cls.counter += 1
        suffix = cls.counter
        tracking = tracking or "T-P7-SYNC-%s-%s" % (code, suffix)
        values = {
            "bosta_delivery_id": "P7-SYNC-%s-%s" % (code, suffix),
            "tracking_number": tracking,
            "creation_source": "SHOPIFY",
            "delivery_type_code": 10,
            "delivery_type_value": "Send",
            "state_value": "Picked Up",
            "bosta_created_at": cls.cutoff + timedelta(days=2),
            "collected_from_business_at": cls.cutoff + timedelta(days=2, hours=2),
            "package_description": "Product %s x 1 (088.01-%s.050)" % (code, code),
        }
        items = None
        if external_id:
            items = [{
                "sequence": 10,
                "external_product_id": external_id,
                "title": "Product %s" % code,
                "quantity": 1,
            }]
        return {"values": values, "items": items, "timeline": None, "source_kind": "search"}

    def _run_sync(self, config, payloads):
        def iterator(**_kwargs):
            return iter(payloads)

        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase7-sync-key"}, clear=False), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaExtractionService") as extraction_class:
            extraction_class.return_value.iter_normalized_search_deliveries.side_effect = iterator
            return config.sudo().action_sync_bosta_deliveries()

    def test_sync_disabled_creates_zero_inventory_effects_or_pickings(self):
        product = self._product("P7 Sync Disabled", "S701", "main", False)
        self._stock(product, 5)
        payload = self._normalized("S701")
        self.config.write({"inventory_sync_enabled": False})
        self._run_sync(self.config, [payload])
        delivery = self.env["bosta.delivery"].sudo().search([
            ("company_id", "=", self.company.id),
            ("tracking_number", "=", payload["values"]["tracking_number"]),
        ], limit=1)
        self.assertTrue(delivery)
        self.assertFalse(self.env["bosta.inventory.effect"].sudo().search([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ]))
        self.assertFalse(self.env["stock.picking"].sudo().search([
            ("origin", "like", "BOSTA/%s/" % delivery.tracking_number)
        ]))
        self.assertEqual(self._qty(product), 5)

    def test_sync_enabled_applies_outbound_once_and_repeat_is_idempotent(self):
        product = self._product("P7 Sync Enabled", "S702", "main", False)
        self._stock(product, 5)
        payload = self._normalized("S702")
        self._run_sync(self.config, [payload])
        delivery = self.env["bosta.delivery"].sudo().search([
            ("company_id", "=", self.company.id),
            ("tracking_number", "=", payload["values"]["tracking_number"]),
        ], limit=1)
        effect = self.env["bosta.inventory.effect"].sudo().search([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ], limit=1)
        self.assertEqual(effect.status, "outbound_applied")
        first_picking = effect.outbound_picking_id
        self.assertTrue(first_picking)
        self.assertEqual(self._qty(product), 4)

        self._run_sync(self.config, [payload])
        effect.invalidate_recordset()
        self.assertEqual(effect.outbound_picking_id, first_picking)
        self.assertEqual(self._qty(product), 4)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([
            ("origin", "=", first_picking.origin)
        ]), 1)

    def test_mapping_tester_or_stock_block_does_not_prevent_following_valid_delivery(self):
        tester_missing = self._product("P7 Tester Missing", "S703T", "main", True)
        self._stock(tester_missing, 5)
        stock_blocked = self._product("P7 Stock Blocked", "S703S", "main", False)
        valid = self._product("P7 Valid After Blocks", "S704", "main", False)
        self._stock(valid, 5)

        payloads = [
            self._normalized("S703M"),
            self._normalized("S703T"),
            self._normalized("S703S"),
            self._normalized("S704"),
        ]
        self._run_sync(self.config, payloads)

        expected = ["blocked_mapping", "blocked_tester", "blocked_stock", "outbound_applied"]
        effects = []
        for payload in payloads:
            delivery = self.env["bosta.delivery"].sudo().search([
                ("company_id", "=", self.company.id),
                ("tracking_number", "=", payload["values"]["tracking_number"]),
            ], limit=1)
            effects.append(self.env["bosta.inventory.effect"].sudo().search([
                ("delivery_id", "=", delivery.id)
            ], limit=1))
        self.assertEqual([effect.status for effect in effects], expected)
        self.assertFalse(effects[0].outbound_picking_id)
        self.assertFalse(effects[1].outbound_picking_id)
        self.assertFalse(effects[2].outbound_picking_id)
        self.assertTrue(effects[3].outbound_picking_id)
        self.assertEqual(self._qty(tester_missing), 5)
        self.assertEqual(self._qty(stock_blocked), 0)
        self.assertEqual(self._qty(valid), 4)

    def test_busy_same_config_retry_does_not_enter_inventory_engine(self):
        delivery = self._delivery("S705", stage="pre_pickup", collected=False)
        before_effects = self.env["bosta.inventory.effect"].sudo().search_count([
            ("company_id", "=", self.company.id)
        ])
        before_pickings = self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%")
        ])
        with patch.object(type(self.config), "_try_acquire_sync_lock", return_value=False), \
             patch("odoo.addons.bosta_integration.models.bosta_config.BostaInventoryService") as inventory_class, \
             self.assertRaises(UserError):
            self.config.sudo().action_process_pending_inventory()
        inventory_class.assert_not_called()
        self.assertEqual(self.env["bosta.inventory.effect"].sudo().search_count([
            ("company_id", "=", self.company.id)
        ]), before_effects)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([
            ("origin", "like", "BOSTA/%")
        ]), before_pickings)
        self.assertTrue(delivery)


    def test_same_config_advisory_lock_blocks_concurrent_phase7_entry_without_duplicates(self):
        product = self._product("P7 Concurrent", "S707", "main", False)
        self._stock(product, 5)
        payload = self._normalized("S707")
        self._run_sync(self.config, [payload])
        delivery = self.env["bosta.delivery"].sudo().search([
            ("company_id", "=", self.company.id),
            ("tracking_number", "=", payload["values"]["tracking_number"]),
        ], limit=1)
        effect = self.env["bosta.inventory.effect"].sudo().search([
            ("delivery_id", "=", delivery.id)
        ], limit=1)
        first_picking = effect.outbound_picking_id
        effect_count = self.env["bosta.inventory.effect"].sudo().search_count([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ])
        picking_count = self.env["stock.picking"].sudo().search_count([
            ("origin", "=", first_picking.origin)
        ])

        self.assertTrue(self.config._try_acquire_sync_lock())
        try:
            with self.registry.cursor() as other_cr:
                other_env = api.Environment(other_cr, self.env.uid, {})
                other_config = other_env["bosta.integration.config"].sudo().browse(self.config.id)
                self.assertFalse(other_config._try_acquire_sync_lock())
        finally:
            self.config._release_sync_lock()

        self.assertEqual(self.env["bosta.inventory.effect"].sudo().search_count([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ]), effect_count)
        self.assertEqual(self.env["stock.picking"].sudo().search_count([
            ("origin", "=", first_picking.origin)
        ]), picking_count)
        self.assertEqual(self._qty(product), 4)

    def test_different_company_configs_are_inventory_independent(self):
        product_a = self._product("P7 Company A", "S706", "main", False)
        self._stock(product_a, 5)

        company_b, warehouse_b, source_b, transit_b = self._make_company_stock("Bosta P7 Sync Company B")
        tmpl_b = self.env["product.template"].sudo().with_company(company_b).create({
            "name": "P7 Company B", "default_code": "S706", "type": "consu",
            "is_storable": True, "tracking": "none", "company_id": company_b.id,
        })
        product_b = tmpl_b.product_variant_id
        product_b.write({"bosta_product_role": "main", "bosta_tester_required": False})
        self.env["stock.quant"].sudo().with_company(company_b)._update_available_quantity(
            product_b, source_b, 7
        )
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase7-sync-key"}, clear=False):
            config_b = self.env["bosta.integration.config"].sudo().with_company(company_b).create({
                "company_id": company_b.id,
                "integration_enabled": True,
                "inventory_sync_enabled": True,
                "inventory_effective_from": self.cutoff,
                "stock_source_location_id": source_b.id,
                "bosta_transit_location_id": transit_b.id,
                "stock_picking_type_id": warehouse_b.int_type_id.id,
            })

        payload_a = self._normalized("S706", tracking="T-P7-COMPANY-A")
        payload_b = self._normalized("S706", tracking="T-P7-COMPANY-B")
        self._run_sync(self.config, [payload_a])
        self._run_sync(config_b, [payload_b])

        qty_a = product_a.sudo().with_company(self.company).with_context(location=self.source.id).qty_available
        qty_b = product_b.sudo().with_company(company_b).with_context(location=source_b.id).qty_available
        self.assertEqual(qty_a, 4)
        self.assertEqual(qty_b, 6)
        effects_a = self.env["bosta.inventory.effect"].sudo().search([
            ("company_id", "=", self.company.id)
        ])
        effects_b = self.env["bosta.inventory.effect"].sudo().search([
            ("company_id", "=", company_b.id)
        ])
        self.assertTrue(effects_a)
        self.assertTrue(effects_b)
        self.assertTrue(all(effect.outbound_picking_id.company_id == self.company for effect in effects_a))
        self.assertTrue(all(effect.outbound_picking_id.company_id == company_b for effect in effects_b))
