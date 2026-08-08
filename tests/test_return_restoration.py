from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_inventory_service import BostaInventoryService
from ..services.bosta_return_service import BostaReturnService
from .test_inventory_effects import Phase7InventoryMixin


class TestPhase8Returns(Phase7InventoryMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock("Bosta P8 Returns")
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase8-key"}, clear=False):
            cls.config = cls.env["bosta.integration.config"].sudo().with_company(cls.company).create({
                "company_id": cls.company.id,
                "integration_enabled": True,
                "inventory_sync_enabled": True,
                "inventory_effective_from": cls.cutoff,
                "stock_source_location_id": cls.source.id,
                "bosta_transit_location_id": cls.transit.id,
                "stock_picking_type_id": cls.warehouse.int_type_id.id,
            })
        manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Phase 8 Manager", "login": "phase8-manager", "email": "phase8-manager@example.invalid",
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [manager_group.id])],
        })
        cls.integration_user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Phase 8 User", "login": "phase8-user", "email": "phase8-user@example.invalid",
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [user_group.id])],
        })

    def _return_service(self):
        return BostaReturnService(self.env, self.config)

    def _forward(self, code, qty=1, tester=True, delivered=False):
        if tester:
            main, tester_product = self._pair(code, stock=10)
        else:
            main = self._product("P8 MAIN " + code, code, "main", False)
            tester_product = False
            self._stock(main, 10)
        delivery = self._delivery(code, qty=qty, stage="with_bosta", flow="forward")
        inventory = BostaInventoryService(self.env, self.config)
        effect = inventory.process_delivery(delivery)
        if delivered:
            delivery.sudo().write({"lifecycle_stage": "delivered_to_customer", "return_scenario": "none"})
            effect = inventory.process_delivery(delivery)
        return delivery, effect, main, tester_product

    def _rto(self, code, original=False, stage="returned_to_origin", scenario="pre_delivery_return", business_reference=False):
        return self._delivery(
            code,
            stage=stage,
            flow="rto",
            collected=False,
            return_scenario=scenario,
            original_delivery_id=original.id if original else False,
            business_reference=business_reference or False,
        )

    def _customer_return(self, code, original=False, stage="customer_return_completed", scenario="post_delivery_customer_return"):
        return self._delivery(
            code,
            stage=stage,
            flow="customer_return",
            collected=False,
            return_scenario=scenario,
            original_delivery_id=original.id if original else False,
        )

    def test_01_rto_without_original_link_no_restoration(self):
        original, _effect, main, tester = self._forward("801")
        rto = self._rto("801")
        case = self._return_service().process_delivery(rto)
        self.assertEqual(case.state, "pending_link")
        self.assertFalse(case.original_delivery_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_02_customer_return_without_original_link_no_restoration(self):
        original, _effect, main, tester = self._forward("802", delivered=True)
        ret = self._customer_return("802")
        case = self._return_service().process_delivery(ret)
        self.assertEqual(case.state, "pending_link")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_03_business_reference_alone_never_auto_links(self):
        original, _effect, _main, _tester = self._forward("803")
        original.write({"business_reference": "SAME-REF"})
        rto = self._rto("803", business_reference="SAME-REF")
        case = self._return_service().process_delivery(rto)
        self.assertFalse(rto.original_delivery_id)
        self.assertFalse(case.original_delivery_id)

    def test_04_duplicate_business_reference_never_guesses(self):
        first, _e1, _m1, _t1 = self._forward("804A", tester=False)
        second, _e2, _m2, _t2 = self._forward("804B", tester=False)
        first.write({"business_reference": "DUP-REF"})
        second.write({"business_reference": "DUP-REF"})
        rto = self._rto("804R", business_reference="DUP-REF")
        case = self._return_service().process_delivery(rto)
        self.assertEqual(case.state, "pending_link")
        self.assertFalse(case.original_delivery_id)

    def test_05_manual_manager_linking_succeeds(self):
        original, _effect, main, tester = self._forward("805")
        rto = self._rto("805", stage="returning_to_origin")
        case = self._return_service().process_delivery(rto)
        user_case = case.with_user(self.manager).with_context(allowed_company_ids=[self.company.id])
        user_case.write({"link_candidate_delivery_id": original.id})
        user_case.action_link_original_delivery()
        self.assertEqual(case.original_delivery_id, original)
        self.assertEqual(rto.original_delivery_id, original)
        self.assertEqual(case.state, "awaiting_physical_return")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_06_ordinary_user_cannot_link(self):
        original, _effect, _main, _tester = self._forward("806")
        rto = self._rto("806")
        case = self._return_service().process_delivery(rto)
        user_case = case.with_user(self.integration_user).with_context(allowed_company_ids=[self.company.id])
        with self.assertRaises(AccessError):
            user_case.write({"link_candidate_delivery_id": original.id})

    def test_07_cross_company_link_rejected(self):
        company_b, _warehouse_b, _source_b, _transit_b = self._make_company_stock("Bosta P8 Other Co")
        original_b = self.env["bosta.delivery"].sudo().with_company(company_b).create({
            "company_id": company_b.id, "bosta_delivery_id": "p8-other-original", "tracking_number": "P8-OTHER-ORIGINAL",
            "delivery_type_code": 10, "delivery_type_value": "Send",
        })
        rto = self._rto("807")
        case = self._return_service().process_delivery(rto)
        with self.assertRaises(UserError):
            self._return_service().link_original(case, original_b)

    def test_08_self_link_rejected(self):
        rto = self._rto("808")
        case = self._return_service().process_delivery(rto)
        with self.assertRaises(UserError):
            self._return_service().link_original(case, rto)

    def test_09_return_to_non_forward_original_rejected(self):
        candidate = self._rto("809A", stage="returning_to_origin")
        rto = self._rto("809B")
        case = self._return_service().process_delivery(rto)
        with self.assertRaises(UserError):
            self._return_service().link_original(case, candidate)

    def test_10_conflicting_relation_not_overwritten(self):
        first, _e1, _m1, _t1 = self._forward("810A", tester=False)
        second, _e2, _m2, _t2 = self._forward("810B", tester=False)
        rto = self._rto("810R", original=first, stage="returning_to_origin")
        case = self._return_service().process_delivery(rto)
        with self.assertRaises(UserError):
            self._return_service().link_original(case, second)
        self.assertEqual(rto.original_delivery_id, first)

    def test_11_returning_to_origin_zero_restoration(self):
        original, _effect, main, tester = self._forward("811")
        case = self._return_service().process_delivery(self._rto("811", original, stage="returning_to_origin"))
        self.assertEqual(case.state, "awaiting_physical_return")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (1, 1))

    def test_12_13_returned_to_origin_restores_main_and_tester(self):
        original, _effect, main, tester = self._forward("812")
        case = self._return_service().process_delivery(self._rto("812", original))
        self.assertEqual(case.state, "restored")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))
        self.assertEqual((self._qty(main, self.transit), self._qty(tester, self.transit)), (0, 0))
        roles = set(case.restoration_effect_id.line_ids.mapped("role"))
        self.assertEqual(roles, {"main", "tester"})

    def test_14_rto_quantity_equals_original_snapshot(self):
        original, effect, main, tester = self._forward("814", qty=2)
        case = self._return_service().process_delivery(self._rto("814", original))
        self.assertEqual(case.state, "restored")
        quantities = {(line.role, line.product_id.id): line.quantity for line in case.restoration_effect_id.line_ids}
        self.assertEqual(quantities[("main", main.id)], effect.line_ids[0].main_quantity)
        self.assertEqual(quantities[("tester", tester.id)], effect.line_ids[0].tester_quantity)

    def test_15_mapping_change_after_outbound_restores_old_snapshot_product(self):
        original, effect, old_main, tester = self._forward("815")
        new_main = self._product("P8 NEW MAIN 815", "NEW815", "main", False)
        mapping = effect.line_ids[0].mapping_id
        mapping.write({"odoo_product_id": new_main.id})
        case = self._return_service().process_delivery(self._rto("815", original))
        self.assertEqual(case.state, "restored")
        self.assertIn(old_main, case.restoration_effect_id.line_ids.mapped("product_id"))
        self.assertNotIn(new_main, case.restoration_effect_id.line_ids.mapped("product_id"))

    def test_16_location_change_restores_original_transit_to_original_source(self):
        original, effect, main, tester = self._forward("816")
        locations_root = self.env.ref("stock.stock_location_locations")
        source_b = self.env["stock.location"].sudo().with_company(self.company).create({
            "name": "P8 Source B", "usage": "internal", "company_id": self.company.id, "location_id": locations_root.id,
        })
        transit_b = self.env["stock.location"].sudo().with_company(self.company).create({
            "name": "P8 Transit B", "usage": "transit", "company_id": self.company.id, "location_id": locations_root.id,
        })
        self.config.write({"stock_source_location_id": source_b.id, "bosta_transit_location_id": transit_b.id})
        case = self._return_service().process_delivery(self._rto("816", original))
        line = case.restoration_effect_id.line_ids[0]
        self.assertEqual(line.source_location_id, effect.transit_location_id)
        self.assertEqual(line.destination_location_id, effect.source_location_id)
        self.assertEqual(self._qty(main, self.source), 10)
        self.assertEqual(self._qty(tester, self.source), 10)
        self.assertEqual(self._qty(main, transit_b), 0)

    def test_17_18_repeated_rto_processing_is_exactly_once(self):
        original, _effect, main, tester = self._forward("817")
        rto = self._rto("817", original)
        service = self._return_service()
        case = service.process_delivery(rto)
        picking = case.restoration_picking_id
        service.process_delivery(rto)
        case.action_retry_restoration()
        self.assertEqual(case.restoration_picking_id, picking)
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))
        self.assertEqual(self.env["stock.picking"].sudo().search_count([("origin", "=", picking.origin)]), 1)

    def test_19_rto_without_original_outbound_effect_blocks(self):
        original = self._delivery("819", stage="with_bosta", flow="forward")
        case = self._return_service().process_delivery(self._rto("819", original))
        self.assertEqual(case.state, "blocked")
        self.assertEqual(case.reason_code, "missing_original_outbound_effect")
        self.assertFalse(case.restoration_picking_id)

    def test_20_original_never_left_warehouse_no_compensating_restoration(self):
        main, tester = self._pair("820", stock=10)
        original = self._delivery("820", stage="pre_pickup", flow="forward", collected=False)
        effect = BostaInventoryService(self.env, self.config).process_delivery(original)
        self.assertFalse(effect.outbound_picking_id)
        case = self._return_service().process_delivery(self._rto("820", original))
        self.assertEqual(case.state, "blocked")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_21_rto_record_creates_no_outbound_deduction(self):
        original, _effect, main, tester = self._forward("821")
        rto = self._rto("821", original, stage="returning_to_origin")
        reverse_effect = BostaInventoryService(self.env, self.config).process_delivery(rto)
        self.assertEqual(reverse_effect.status, "not_applicable")
        self.assertFalse(reverse_effect.outbound_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_22_customer_return_pickup_zero_restoration(self):
        original, _effect, main, tester = self._forward("822", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("822", original, stage="customer_return_pickup"))
        self.assertEqual(case.state, "awaiting_physical_return")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_23_customer_return_completed_pending_inspection_zero_restoration(self):
        original, _effect, main, tester = self._forward("823", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("823", original))
        self.assertEqual(case.state, "awaiting_inspection")
        self.assertEqual(case.inspection_state, "pending")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_24_customer_return_rejected_zero_restoration(self):
        original, _effect, main, tester = self._forward("824", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("824", original))
        case.sudo().action_reject_returned_product()
        self.assertEqual((case.state, case.inspection_state), ("rejected", "rejected"))
        self.assertFalse(case.restoration_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_25_26_customer_return_accept_restores_main_only(self):
        original, _effect, main, tester = self._forward("825", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("825", original))
        self.assertEqual(len(case.return_line_ids), 1)
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        self.assertEqual(case.state, "restored")
        self.assertEqual(self._qty(main), 10)
        self.assertEqual(self._qty(tester), 9)
        roles = case.restoration_effect_id.line_ids.mapped("role")
        self.assertEqual(roles, ["main"])

    def test_27_repeated_customer_accept_exactly_once(self):
        original, _effect, main, tester = self._forward("827", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("827", original))
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        picking = case.restoration_picking_id
        case.sudo().action_accept_returned_product()
        self.assertEqual(case.restoration_picking_id, picking)
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 9))

    def test_28_returned_quantity_cannot_exceed_original_delivered(self):
        original, _effect, _main, _tester = self._forward("828", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("828", original))
        with self.assertRaises(ValidationError):
            case.return_line_ids.sudo().write({"returned_quantity": 2})

    def test_29_explicit_partial_customer_return_is_supported(self):
        original, _effect, main, tester = self._forward("829", qty=2, delivered=True)
        case = self._return_service().process_delivery(self._customer_return("829", original))
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        self.assertEqual(case.state, "restored")
        self.assertEqual(self._qty(main), 9)
        self.assertEqual(self._qty(tester), 8)
        self.assertEqual(case.restoration_effect_id.line_ids[0].quantity, 1)

    def test_30_no_partial_or_full_return_invented_from_lifecycle(self):
        original, _effect, main, tester = self._forward("830", delivered=True)
        case = self._return_service().process_delivery(self._customer_return("830", original))
        case.sudo().action_accept_returned_product()
        self.assertEqual(case.state, "awaiting_inspection")
        self.assertEqual(case.reason_code, "returned_quantity_required")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_31_customer_return_record_creates_no_outbound_deduction(self):
        original, _effect, main, tester = self._forward("831", delivered=True)
        ret = self._customer_return("831", original, stage="customer_return_pickup")
        reverse_effect = BostaInventoryService(self.env, self.config).process_delivery(ret)
        self.assertEqual(reverse_effect.status, "not_applicable")
        self.assertFalse(reverse_effect.outbound_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_32_customer_return_linked_to_non_delivered_original_blocks(self):
        original, _effect, main, tester = self._forward("832", delivered=False)
        case = self._return_service().process_delivery(self._customer_return("832", original))
        self.assertEqual(case.state, "blocked")
        self.assertEqual(case.reason_code, "original_not_delivered_in_inventory")
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_33_36_terminal_uncertainty_never_restores(self):
        original, _effect, main, tester = self._forward("833")
        for suffix, stage, scenario in [
            ("L", "lost", "lost"),
            ("D", "damaged", "damaged"),
            ("T", "terminated", "pre_delivery_return"),
            ("A", "ambiguous", "ambiguous"),
        ]:
            case = self._return_service().process_delivery(self._rto("833" + suffix, original, stage=stage, scenario=scenario))
            self.assertEqual(case.state, "review_required")
            self.assertFalse(case.restoration_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (9, 9))

    def test_39_restoration_snapshots_immutable_after_apply(self):
        original, _effect, _main, _tester = self._forward("839")
        case = self._return_service().process_delivery(self._rto("839", original))
        line = case.restoration_effect_id.line_ids[0]
        with self.assertRaises(ValidationError):
            line.with_context(bosta_return_engine=True).write({"quantity": line.quantity + 1})
        with self.assertRaises(ValidationError):
            case.restoration_effect_id.with_context(bosta_return_engine=True).write({"rule_code": "changed"})

    def test_40_41_mapping_and_config_edits_do_not_rewrite_restoration_history(self):
        original, original_effect, _main, _tester = self._forward("840")
        case = self._return_service().process_delivery(self._rto("840", original))
        snapshots = [(l.product_id.id, l.source_location_id.id, l.destination_location_id.id, l.quantity) for l in case.restoration_effect_id.line_ids]
        mapping = original_effect.line_ids[0].mapping_id
        replacement = self._product("P8 replacement 840", "R840", "main", False)
        mapping.write({"odoo_product_id": replacement.id})
        locations_root = self.env.ref("stock.stock_location_locations")
        source_b = self.env["stock.location"].sudo().with_company(self.company).create({
            "name": "P8 Post Source", "usage": "internal", "company_id": self.company.id, "location_id": locations_root.id,
        })
        transit_b = self.env["stock.location"].sudo().with_company(self.company).create({
            "name": "P8 Post Transit", "usage": "transit", "company_id": self.company.id, "location_id": locations_root.id,
        })
        self.config.write({"stock_source_location_id": source_b.id, "bosta_transit_location_id": transit_b.id})
        after = [(l.product_id.id, l.source_location_id.id, l.destination_location_id.id, l.quantity) for l in case.restoration_effect_id.line_ids]
        self.assertEqual(after, snapshots)

    def test_42_db_uniqueness_blocks_duplicate_restoration_effect(self):
        original, _effect, _main, _tester = self._forward("842")
        case = self._return_service().process_delivery(self._rto("842", original))
        self.assertEqual(case.restoration_effect_id.status, "applied")
        Effect = self.env["bosta.return.restoration.effect"].sudo().with_context(bosta_return_engine=True).with_company(self.company)
        with self.env.cr.savepoint(), self.assertRaises(IntegrityError):
            Effect.create({
                "company_id": self.company.id,
                "return_case_id": case.id,
                "return_delivery_id": case.return_delivery_id.id,
                "original_delivery_id": case.original_delivery_id.id,
                "return_type": case.return_type,
                "status": "pending",
            })

    def test_43_second_return_cannot_over_restore_original(self):
        original, _effect, main, tester = self._forward("843")
        first = self._return_service().process_delivery(self._rto("843A", original))
        self.assertEqual(first.state, "restored")
        second = self._return_service().process_delivery(self._rto("843B", original))
        self.assertEqual(second.state, "blocked")
        self.assertIn(second.reason_code, {"original_main_already_restored", "original_tester_already_restored"})
        self.assertFalse(second.restoration_picking_id)
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_44_retry_after_fixing_missing_link_applies_once(self):
        original, _effect, main, tester = self._forward("844")
        rto = self._rto("844")
        case = self._return_service().process_delivery(rto)
        self.assertEqual(case.state, "pending_link")
        self._return_service().link_original(case, original)
        self._return_service().process_case(case)
        picking = case.restoration_picking_id
        self._return_service().process_case(case)
        self.assertEqual(case.restoration_picking_id, picking)
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))

    def test_45_blocked_return_does_not_prevent_next_valid_delivery(self):
        missing_original = self._delivery("845X", stage="with_bosta", flow="forward")
        blocked = self._return_service().process_delivery(self._rto("845X", missing_original))
        valid_original, _effect, main, tester = self._forward("845V")
        valid = self._return_service().process_delivery(self._rto("845V", valid_original))
        self.assertEqual(blocked.state, "blocked")
        self.assertEqual(valid.state, "restored")
        self.assertEqual((self._qty(main), self._qty(tester)), (10, 10))
