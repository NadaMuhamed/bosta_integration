from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase

from ..services.bosta_financial_service import BostaFinancialService
from ..services.bosta_inventory_service import BostaInventoryService
from ..services.bosta_persistence_service import BostaPersistenceService
from ..services.bosta_return_service import BostaReturnService
from ..models import bosta_config as bosta_config_model
from .test_inventory_effects import Phase7InventoryMixin


class TestPhase9FinancialRuntime(Phase7InventoryMixin, TransactionCase):
    """Runtime financial tests.

    These complement the pure/static Phase 9 baseline.  They intentionally use
    the accepted Phase 7 inventory and Phase 8 restoration services instead of
    manufacturing parallel stock/return evidence for finance.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company, cls.warehouse, cls.source, cls.transit = cls._make_company_stock(
            "Bosta P9 Finance"
        )
        with patch.dict("os.environ", {"BOSTA_API_KEY": "synthetic-phase9-key"}, clear=False):
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
            "name": "Phase 9 Manager",
            "login": "phase9-manager",
            "email": "phase9-manager@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [manager_group.id])],
        })
        cls.integration_user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Phase 9 User",
            "login": "phase9-user",
            "email": "phase9-user@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [user_group.id])],
        })

    def _financial_service(self):
        return BostaFinancialService(self.env, self.config)

    def _return_service(self):
        return BostaReturnService(self.env, self.config)

    def _set_authoritative_fee(self, delivery, amount):
        delivery.sudo().with_context(bosta_delivery_persistence=True).write({
            "shipment_fees": amount,
            "shipment_fees_present": True,
            "pricing_currency_code": self.company.currency_id.name,
            "pricing_currency_code_present": True,
        })

    def _forward(
        self,
        code,
        *,
        qty=1,
        tester=True,
        delivered=True,
        main_cost=83.0,
        tester_cost=7.0,
        fee=25.0,
        fee_present=True,
        cod=None,
    ):
        if tester:
            main, tester_product = self._pair(code, stock=10)
        else:
            main = self._product("P9 MAIN " + code, code, "main", False)
            tester_product = False
            self._stock(main, 10)
        main.sudo().with_company(self.company).write({"standard_price": main_cost})
        if tester_product:
            tester_product.sudo().with_company(self.company).write({"standard_price": tester_cost})

        delivery = self._delivery(code, qty=qty, stage="with_bosta", flow="forward")
        if cod is not None:
            delivery.sudo().with_context(bosta_delivery_persistence=True).write({"cod_amount": cod, "cod_amount_present": True})
        if fee_present:
            self._set_authoritative_fee(delivery, fee)

        inventory = BostaInventoryService(self.env, self.config)
        effect = inventory.process_delivery(delivery)
        if delivered:
            delivery.sudo().write({
                "lifecycle_stage": "delivered_to_customer",
                "return_scenario": "none",
            })
            effect = inventory.process_delivery(delivery)
        return delivery, effect, main, tester_product

    def _confirm_revenue(self, financial, amount):
        manager_financial = financial.with_user(self.manager).with_context(
            allowed_company_ids=[self.company.id]
        )
        manager_financial.write({
            "manual_revenue_input": amount,
            "manual_override_reason": "phase9_runtime_test",
        })
        manager_financial.action_confirm_revenue()
        financial.invalidate_recordset()
        return financial

    def _confirm_return_fee(self, financial, amount):
        manager_financial = financial.with_user(self.manager).with_context(
            allowed_company_ids=[self.company.id]
        )
        manager_financial.write({
            "manual_return_fee_input": amount,
            "manual_override_reason": "phase9_runtime_test",
        })
        manager_financial.action_confirm_return_fee()
        financial.invalidate_recordset()
        return financial

    def _rto(self, code, original, stage="returned_to_origin"):
        return self._delivery(
            code,
            stage=stage,
            flow="rto",
            collected=False,
            return_scenario="pre_delivery_return",
            original_delivery_id=original.id,
        )

    def _customer_return(self, code, original, stage="customer_return_completed"):
        return self._delivery(
            code,
            stage=stage,
            flow="customer_return",
            collected=False,
            return_scenario="post_delivery_customer_return",
            original_delivery_id=original.id,
        )

    def test_01_positive_cod_is_not_recognized_revenue_by_default(self):
        delivery, _effect, _main, _tester = self._forward("901", cod=199)
        financial = self._financial_service().process_delivery(delivery)
        self.assertEqual(financial.revenue_source, "not_available")
        self.assertEqual(financial.financial_status, "incomplete")

    def test_02_missing_fee_stays_unavailable_not_zero(self):
        delivery, _effect, _main, _tester = self._forward("902", fee_present=False)
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 199)
        self.assertEqual(financial.logistics_cost_status, "unavailable")
        self.assertEqual(financial.logistics_cost_source, "unavailable")
        self.assertEqual(financial.financial_status, "incomplete")

    def test_03_explicit_zero_fee_is_authoritative_zero(self):
        delivery, _effect, _main, _tester = self._forward("903", tester=False, main_cost=83, fee=0)
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 199)
        self.assertEqual(financial.logistics_cost_status, "authoritative")
        self.assertEqual(financial.logistics_cost_amount, 0)
        self.assertEqual(financial.financial_status, "calculated")
        self.assertEqual(financial.contribution_profit, 116)

    def test_04_delivered_main_and_tester_calculate_expected_contribution(self):
        delivery, _effect, _main, _tester = self._forward("904")
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 199)
        self.assertEqual(financial.main_cogs_amount, 83)
        self.assertEqual(financial.tester_cogs_amount, 7)
        self.assertEqual(financial.gross_cogs_amount, 90)
        self.assertEqual(financial.net_cogs_amount, 90)
        self.assertEqual(financial.logistics_cost_amount, 25)
        self.assertEqual(financial.contribution_profit, 84)
        self.assertEqual(financial.financial_status, "calculated")

    def test_05_quantity_two_multiplies_snapshotted_cost(self):
        delivery, _effect, _main, _tester = self._forward("905", qty=2)
        financial = self._financial_service().process_delivery(delivery)
        self.assertEqual(financial.main_cogs_amount, 166)
        self.assertEqual(financial.tester_cogs_amount, 14)
        self.assertEqual(financial.gross_cogs_amount, 180)

    def test_06_no_required_tester_creates_no_fake_tester_cogs(self):
        delivery, _effect, _main, _tester = self._forward("906", tester=False)
        financial = self._financial_service().process_delivery(delivery)
        self.assertEqual(financial.tester_cogs_amount, 0)
        self.assertFalse(financial.line_ids.filtered(lambda line: line.role == "tester"))

    def test_07_standard_price_change_does_not_rewrite_cost_snapshot(self):
        delivery, _effect, main, _tester = self._forward("907", tester=False, main_cost=83)
        service = self._financial_service()
        financial = service.process_delivery(delivery)
        line = financial.line_ids
        self.assertEqual(line.unit_cost, 83)
        main.sudo().with_company(self.company).write({"standard_price": 123})
        service.process_delivery(delivery)
        line.invalidate_recordset()
        self.assertEqual(line.unit_cost, 83)
        self.assertEqual(line.cost_source, "product_standard_price")

    def test_08_repeated_calculation_reuses_snapshot_and_lines(self):
        delivery, _effect, _main, _tester = self._forward("908")
        service = self._financial_service()
        financial = service.process_delivery(delivery)
        financial_id = financial.id
        line_ids = financial.line_ids.ids
        service.process_delivery(delivery)
        second = service.process_delivery(delivery)
        self.assertEqual(second.id, financial_id)
        self.assertEqual(second.line_ids.ids, line_ids)
        self.assertEqual(self.env["bosta.delivery.financial"].sudo().search_count([
            ("company_id", "=", self.company.id), ("delivery_id", "=", delivery.id)
        ]), 1)

    def test_09_db_uniqueness_blocks_duplicate_financial_snapshot(self):
        delivery, _effect, _main, _tester = self._forward("909")
        financial = self._financial_service().process_delivery(delivery)
        Financial = self.env["bosta.delivery.financial"].sudo().with_context(
            bosta_financial_engine=True
        ).with_company(self.company)
        with self.env.cr.savepoint(), self.assertRaises(IntegrityError):
            Financial.create({
                "company_id": self.company.id,
                "delivery_id": financial.delivery_id.id,
                "currency_id": self.company.currency_id.id,
            })

    def test_10_financial_calculation_does_not_mutate_stock(self):
        delivery, _effect, main, tester = self._forward("910")
        before = (self._qty(main), self._qty(tester))
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 199)
        self.assertEqual((self._qty(main), self._qty(tester)), before)

    def test_11_manager_cost_override_is_audited_and_idempotent(self):
        delivery, _effect, _main, _tester = self._forward("911", tester=False, main_cost=83)
        financial = self._financial_service().process_delivery(delivery)
        line = financial.line_ids.with_user(self.manager).with_context(
            allowed_company_ids=[self.company.id]
        )
        line.write({"manual_unit_cost_input": 80, "manual_override_reason": "known_cost"})
        line.action_confirm_cost_override()
        line.invalidate_recordset()
        self.assertEqual(line.unit_cost, 80)
        self.assertEqual(line.cost_source, "explicit_override")
        self.assertEqual(line.cost_override_reason, "known_cost")
        self.assertEqual(line.cost_overridden_by_id, self.manager)
        first_when = line.cost_overridden_at
        line.action_confirm_cost_override()
        line.invalidate_recordset()
        self.assertEqual(line.unit_cost, 80)
        self.assertTrue(line.cost_overridden_at >= first_when)

    def test_12_ordinary_user_cannot_override_cost_or_revenue(self):
        delivery, _effect, _main, _tester = self._forward("912", tester=False)
        financial = self._financial_service().process_delivery(delivery)
        user_financial = financial.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company.id]
        )
        with self.assertRaises(AccessError):
            user_financial.write({"manual_revenue_input": 199})
        user_line = financial.line_ids.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company.id]
        )
        with self.assertRaises(AccessError):
            user_line.write({"manual_unit_cost_input": 80})

    def test_13_completed_rto_credits_main_and_tester_but_keeps_shipping(self):
        original, _effect, _main, _tester = self._forward("913", delivered=False)
        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 0)
        rto = self._rto("913R", original)
        case = self._return_service().process_delivery(rto)
        self.assertEqual(case.state, "restored")
        service.process_delivery(rto)
        financial.invalidate_recordset()
        self.assertEqual(financial.gross_cogs_amount, 90)
        self.assertEqual(financial.restored_main_cost_credit, 83)
        self.assertEqual(financial.restored_tester_cost_credit, 7)
        self.assertEqual(financial.net_cogs_amount, 0)
        self.assertEqual(financial.logistics_cost_amount, 25)
        self.assertEqual(financial.contribution_profit, -25)

    def test_14_repeated_rto_finance_does_not_double_credit(self):
        original, _effect, _main, _tester = self._forward("914", delivered=False)
        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 0)
        rto = self._rto("914R", original)
        return_service = self._return_service()
        return_service.process_delivery(rto)
        service.process_delivery(rto)
        credits = (financial.restored_main_cost_credit, financial.restored_tester_cost_credit)
        return_service.process_delivery(rto)
        service.process_delivery(rto)
        financial.invalidate_recordset()
        self.assertEqual(
            (financial.restored_main_cost_credit, financial.restored_tester_cost_credit),
            credits,
        )

    def test_15_customer_return_credits_main_only_and_requires_return_fee_evidence(self):
        original, _effect, _main, _tester = self._forward("915", delivered=True)
        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 199)
        ret = self._customer_return("915R", original)
        case = self._return_service().process_delivery(ret)
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        service.process_delivery(ret)
        financial.invalidate_recordset()
        self.assertEqual(financial.restored_main_cost_credit, 83)
        self.assertEqual(financial.restored_tester_cost_credit, 0)
        self.assertEqual(financial.net_cogs_amount, 7)
        self.assertEqual(financial.return_fee_source, "not_available")
        self.assertEqual(financial.financial_status, "incomplete")
        self._confirm_return_fee(financial, 0)
        self.assertEqual(financial.financial_status, "calculated")
        self.assertEqual(financial.contribution_profit, 167)

    def test_16_partial_customer_return_credits_only_approved_quantity(self):
        original, _effect, _main, _tester = self._forward("916", qty=2, delivered=True)
        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 398)
        ret = self._customer_return("916R", original)
        case = self._return_service().process_delivery(ret)
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        service.process_delivery(ret)
        financial.invalidate_recordset()
        self.assertEqual(financial.main_cogs_amount, 166)
        self.assertEqual(financial.restored_main_cost_credit, 83)
        self.assertEqual(financial.restored_tester_cost_credit, 0)

    def test_17_customer_return_rejection_creates_no_cost_credit(self):
        original, _effect, _main, _tester = self._forward("917", delivered=True)
        service = self._financial_service()
        financial = service.process_delivery(original)
        ret = self._customer_return("917R", original)
        case = self._return_service().process_delivery(ret)
        case.sudo().action_reject_returned_product()
        service.process_delivery(ret)
        financial.invalidate_recordset()
        self.assertEqual(financial.restored_main_cost_credit, 0)
        self.assertEqual(financial.restored_tester_cost_credit, 0)

    def test_18_unlinked_rto_cannot_financially_modify_original(self):
        original, _effect, _main, _tester = self._forward("918", delivered=False)
        financial = self._financial_service().process_delivery(original)
        rto = self._delivery(
            "918R", stage="returned_to_origin", flow="rto", collected=False,
            return_scenario="pre_delivery_return", business_reference=original.business_reference,
        )
        self.assertFalse(self._financial_service().process_delivery(rto))
        financial.invalidate_recordset()
        self.assertEqual(financial.restored_main_cost_credit, 0)
        self.assertEqual(financial.restored_tester_cost_credit, 0)

    def test_19_lost_delivery_without_compensation_is_review_required(self):
        delivery, _effect, _main, _tester = self._forward("919", delivered=False)
        delivery.sudo().write({"lifecycle_stage": "lost"})
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 0)
        self.assertEqual(financial.compensation_source, "not_available")
        self.assertEqual(financial.financial_status, "review_required")
        self.assertEqual(financial.restored_main_cost_credit, 0)
        self.assertEqual(financial.restored_tester_cost_credit, 0)

    def test_20_finalize_freezes_historical_cost_snapshot(self):
        delivery, _effect, main, _tester = self._forward("920", tester=False, main_cost=83)
        financial = self._financial_service().process_delivery(delivery)
        self._confirm_revenue(financial, 199)
        manager_financial = financial.with_user(self.manager).with_context(
            allowed_company_ids=[self.company.id]
        )
        manager_financial.action_finalize()
        main.sudo().with_company(self.company).write({"standard_price": 150})
        self._financial_service().process_delivery(delivery)
        financial.invalidate_recordset()
        self.assertEqual(financial.financial_status, "finalized")
        self.assertEqual(financial.line_ids.unit_cost, 83)
        self.assertEqual(financial.contribution_profit, 91)


    def test_26_finalized_base_later_rto_uses_main_and_tester_adjustments_once(self):
        # Preserve Phase 8 behavior: outbound stock has left but has not been
        # finalized to the customer location, so a later authoritative RTO can
        # still restore MAIN + TESTER through the accepted return service.
        original, inventory_effect, _main, _tester = self._forward("926", delivered=False)
        original.sudo().write({"lifecycle_stage": "delivered_to_customer", "return_scenario": "none"})
        self.assertFalse(inventory_effect.final_picking_id)

        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 199)
        self.assertEqual(financial.financial_status, "calculated")
        manager_financial = financial.with_user(self.manager).with_context(allowed_company_ids=[self.company.id])
        manager_financial.action_finalize()
        financial.invalidate_recordset()
        base_contribution = financial.contribution_profit
        base_line_state = {
            line.id: (line.restored_quantity, line.restored_cost_credit, line.net_cost)
            for line in financial.line_ids
        }

        rto = self._rto("926R", original)
        return_service = self._return_service()
        case = return_service.process_delivery(rto)
        self.assertEqual(case.state, "restored")
        service.process_delivery(rto)
        financial.invalidate_recordset()
        adjustments = financial.adjustment_ids.sorted("id")
        self.assertEqual(financial.financial_status, "finalized")
        self.assertEqual(financial.contribution_profit, base_contribution)
        self.assertEqual(
            {line.id: (line.restored_quantity, line.restored_cost_credit, line.net_cost) for line in financial.line_ids},
            base_line_state,
        )
        self.assertEqual(len(adjustments), 2)
        self.assertEqual(set(adjustments.mapped("role")), {"main", "tester"})
        self.assertEqual(sum(adjustments.mapped("amount")), 90)
        self.assertEqual(financial.post_finalize_inventory_credit, 90)
        self.assertEqual(financial.contribution_after_adjustments, base_contribution + 90)

        return_service.process_delivery(rto)
        service.process_delivery(rto)
        financial.invalidate_recordset()
        self.assertEqual(len(financial.adjustment_ids), 2)
        self.assertEqual(sum(financial.adjustment_ids.mapped("amount")), 90)

    def test_27_finalized_base_later_customer_return_adjusts_main_only_once(self):
        original, _effect, _main, _tester = self._forward("927", delivered=True)
        service = self._financial_service()
        financial = service.process_delivery(original)
        self._confirm_revenue(financial, 199)
        manager_financial = financial.with_user(self.manager).with_context(allowed_company_ids=[self.company.id])
        manager_financial.action_finalize()
        financial.invalidate_recordset()
        base_contribution = financial.contribution_profit
        tester_line = financial.line_ids.filtered(lambda line: line.role == "tester")
        tester_base_net = tester_line.net_cost

        ret = self._customer_return("927R", original)
        return_service = self._return_service()
        case = return_service.process_delivery(ret)
        case.return_line_ids.sudo().write({"returned_quantity": 1})
        case.sudo().action_accept_returned_product()
        service.process_delivery(ret)
        financial.invalidate_recordset()
        adjustments = financial.adjustment_ids
        self.assertEqual(financial.financial_status, "finalized")
        self.assertEqual(financial.contribution_profit, base_contribution)
        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjustments.role, "main")
        self.assertEqual(adjustments.amount, 83)
        self.assertEqual(financial.post_finalize_inventory_credit, 83)
        self.assertEqual(financial.contribution_after_adjustments, base_contribution + 83)
        tester_line.invalidate_recordset()
        self.assertEqual(tester_line.net_cost, tester_base_net)

        return_service.process_delivery(ret)
        service.process_delivery(ret)
        financial.invalidate_recordset()
        self.assertEqual(len(financial.adjustment_ids), 1)
        self.assertEqual(financial.adjustment_ids.amount, 83)

    def test_28_manual_create_cannot_inject_api_financial_evidence(self):
        Delivery = self.env["bosta.delivery"].sudo().with_company(self.company)
        protected_values = {
            "cod_amount": 199,
            "original_cod_amount": 199,
            "shipment_fees": 25,
            "shipping_fee": 25,
            "bundle_discount": 5,
            "opening_package_fee": 2,
            "bosta_material_fee": 3,
            "price_before_vat": 20,
            "price_after_vat": 25,
            "vat_rate": 0.14,
            "pricing_currency_code": self.company.currency_id.name,
            "cod_amount_present": True,
            "original_cod_amount_present": True,
            "shipment_fees_present": True,
            "shipping_fee_present": True,
            "bundle_discount_present": True,
            "opening_package_fee_present": True,
            "bosta_material_fee_present": True,
            "price_before_vat_present": True,
            "price_after_vat_present": True,
            "pricing_currency_code_present": True,
        }
        for index, (field_name, field_value) in enumerate(protected_values.items()):
            with self.subTest(field_name=field_name), self.assertRaises(AccessError):
                Delivery.create({
                    "company_id": self.company.id,
                    "bosta_delivery_id": f"manual-financial-injection-{index}",
                    "tracking_number": f"manual-financial-injection-{index}",
                    field_name: field_value,
                })

    def test_29_persistence_service_can_create_and_update_api_financial_evidence(self):
        service = BostaPersistenceService(self.env)
        normalized = {
            "values": {
                "bosta_delivery_id": "p9-persistence-finance",
                "tracking_number": "p9-persistence-finance",
                "cod_amount": 199,
                "shipment_fees": 25,
                "pricing_currency_code": self.company.currency_id.name,
            },
            "items": None,
            "timeline": None,
            "source_kind": "search",
        }
        first = service.upsert_normalized_delivery(normalized, self.company)
        record = first["record"]
        self.assertEqual(first["action"], "created")
        self.assertTrue(record.cod_amount_present)
        self.assertTrue(record.shipment_fees_present)
        self.assertEqual(record.cod_amount, 199)
        self.assertEqual(record.shipment_fees, 25)

        updated = dict(normalized)
        updated["values"] = dict(normalized["values"], shipment_fees=30)
        second = service.upsert_normalized_delivery(updated, self.company)
        record.invalidate_recordset()
        self.assertEqual(second["action"], "updated")
        self.assertEqual(record.shipment_fees, 30)



class TestPhase9CronRuntime(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def _config(self, company, env_var, **extra):
        vals = {
            "company_id": company.id,
            "integration_enabled": True,
            "api_key_env_var": env_var,
            "auto_sync_enabled": True,
            "auto_sync_interval_minutes": 5,
        }
        vals.update(extra)
        with patch.dict("os.environ", {env_var: "synthetic-cron-key"}, clear=False):
            return self.env["bosta.integration.config"].sudo().with_company(company).create(vals)

    def test_21_auto_sync_is_opt_in_by_default(self):
        config = self.env["bosta.integration.config"].sudo().create({
            "company_id": self.company.id,
        })
        self.assertFalse(config.auto_sync_enabled)
        self.assertFalse(config.financial_details_enrichment_enabled)

    def test_22_due_cron_reuses_existing_sync_action_and_advances_schedule(self):
        env_var = "BOSTA_PHASE9_CRON_KEY"
        config = self._config(self.company, env_var)
        due = fields.Datetime.now() - timedelta(minutes=1)
        config._write_auto_sync_state({"next_auto_sync_at": due})

        def fake_sync(record):
            record._write_sync_state({"last_sync_status": "success", "last_sync_error": False})
            return True

        with patch.dict("os.environ", {env_var: "synthetic-cron-key"}, clear=False), \
             patch.object(type(config), "action_sync_bosta_deliveries", autospec=True, side_effect=fake_sync) as sync:
            self.env["bosta.integration.config"]._cron_sync_due_configs()
        config.invalidate_recordset()
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(config.last_auto_sync_status, "success")
        self.assertTrue(config.next_auto_sync_at > due)

    def test_23_not_due_config_does_not_run(self):
        env_var = "BOSTA_PHASE9_CRON_NOT_DUE"
        config = self._config(self.company, env_var)
        config._write_auto_sync_state({"next_auto_sync_at": fields.Datetime.now() + timedelta(hours=1)})
        with patch.dict("os.environ", {env_var: "synthetic-cron-key"}, clear=False), \
             patch.object(type(config), "action_sync_bosta_deliveries", autospec=True) as sync:
            self.env["bosta.integration.config"]._cron_sync_due_configs()
        sync.assert_not_called()

    def test_24_missing_api_key_fails_safely_without_network_sync(self):
        env_var = "BOSTA_PHASE9_MISSING_KEY"
        config = self._config(self.company, env_var)
        config._write_auto_sync_state({"next_auto_sync_at": fields.Datetime.now() - timedelta(minutes=1)})
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(type(config), "action_sync_bosta_deliveries", autospec=True) as sync:
            self.env["bosta.integration.config"]._cron_sync_due_configs()
        config.invalidate_recordset()
        sync.assert_not_called()
        self.assertEqual(config.last_auto_sync_status, "failed")
        self.assertEqual(config.last_auto_sync_error, "api_key_not_configured")

    def test_25_one_config_failure_does_not_stop_next_due_config(self):
        env_var = "BOSTA_PHASE9_ISOLATION_KEY"
        company_a = self.env["res.company"].create({"name": "P9 Cron A"})
        company_b = self.env["res.company"].create({"name": "P9 Cron B"})
        config_a = self._config(company_a, env_var)
        config_b = self._config(company_b, env_var)
        due = fields.Datetime.now() - timedelta(minutes=1)
        config_a._write_auto_sync_state({"next_auto_sync_at": due})
        config_b._write_auto_sync_state({"next_auto_sync_at": due})
        called = []

        def fake_sync(record):
            called.append(record.id)
            if record.id == config_a.id:
                raise RuntimeError("synthetic isolated failure")
            record._write_sync_state({"last_sync_status": "success", "last_sync_error": False})
            return True

        with patch.dict("os.environ", {env_var: "synthetic-cron-key"}, clear=False), \
             patch.object(type(config_a), "action_sync_bosta_deliveries", autospec=True, side_effect=fake_sync):
            self.env["bosta.integration.config"]._cron_sync_due_configs()
        config_a.invalidate_recordset()
        config_b.invalidate_recordset()
        self.assertIn(config_a.id, called)
        self.assertIn(config_b.id, called)
        self.assertEqual(config_a.last_auto_sync_status, "failed")
        self.assertEqual(config_b.last_auto_sync_status, "success")

    def test_30_unexpected_cron_exception_is_redacted_and_isolated(self):
        env_var = "BOSTA_PHASE9_REDACTION_KEY"
        config = self._config(self.company, env_var)
        config._write_auto_sync_state({"next_auto_sync_at": fields.Datetime.now() - timedelta(minutes=1)})
        sensitive = "Authorization: Bearer SUPER_SECRET phone=01000000000 address=private"

        with patch.dict("os.environ", {env_var: "synthetic-cron-key"}, clear=False), \
             patch.object(type(config), "action_sync_bosta_deliveries", autospec=True, side_effect=RuntimeError(sensitive)), \
             patch.object(bosta_config_model._logger, "error") as log_error:
            self.env["bosta.integration.config"]._cron_sync_due_configs()

        config.invalidate_recordset()
        self.assertEqual(config.last_auto_sync_status, "failed")
        self.assertEqual(config.last_auto_sync_error, "unexpected_scheduled_sync_failure")
        log_error.assert_called_once_with(
            "Unexpected scheduled Bosta sync failure; sensitive details redacted"
        )
        rendered_log_args = " ".join(str(arg) for arg in log_error.call_args.args)
        self.assertNotIn("SUPER_SECRET", rendered_log_args)
        self.assertNotIn("01000000000", rendered_log_args)
        self.assertNotIn("private", rendered_log_args)

