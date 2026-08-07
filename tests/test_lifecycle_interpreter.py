from copy import deepcopy
from unittest import TestCase

from ..services.bosta_lifecycle_interpreter import BostaLifecycleInterpreter


class TestBostaLifecycleInterpreter(TestCase):
    def setUp(self):
        self.interpreter = BostaLifecycleInterpreter()

    @staticmethod
    def _normalized(*, flow_code=10, flow_value="Send", state_code=None, state_value=None,
                    timeline=None, source_kind="search", **extra):
        values = {
            "bosta_delivery_id": "phase6-id",
            "tracking_number": "phase6-track",
            "delivery_type_code": flow_code,
            "delivery_type_value": flow_value,
        }
        if state_code is not None:
            values["state_code"] = state_code
        if state_value is not None:
            values["state_value"] = state_value
        values.update(extra)
        return {
            "values": values,
            "items": None,
            "timeline": timeline,
            "source_kind": source_kind,
        }

    def _result(self, **kwargs):
        return self.interpreter.interpret(self._normalized(**kwargs))

    def test_01_forward_processing_before_pickup_is_pre_pickup(self):
        self.assertEqual(self._result(state_value="Processing")["lifecycle_stage"], "pre_pickup")

    def test_02_forward_picked_up_value_is_with_bosta(self):
        self.assertEqual(self._result(state_value="Picked Up")["lifecycle_stage"], "with_bosta")

    def test_03_forward_picked_up_code_21_is_with_bosta(self):
        self.assertEqual(self._result(state_code=21, state_value="Future Label")["lifecycle_stage"], "with_bosta")

    def test_04_forward_collected_timestamp_is_with_bosta(self):
        result = self._result(state_value="Processing", collected_from_business_at="2026-08-08 00:00:00")
        self.assertEqual(result["lifecycle_stage"], "with_bosta")

    def test_05_forward_delivered_is_customer_delivery(self):
        result = self._result(state_value="Delivered")
        self.assertEqual(result["lifecycle_stage"], "delivered_to_customer")
        self.assertEqual(result["return_scenario"], "none")

    def test_06_forward_delivered_code_45_is_customer_delivery(self):
        result = self._result(state_code=45, state_value="Future Label")
        self.assertEqual(result["lifecycle_stage"], "delivered_to_customer")

    def test_07_forward_terminated_before_collection_has_no_invented_return(self):
        result = self._result(state_value="Terminated")
        self.assertEqual(result["lifecycle_stage"], "terminated")
        self.assertEqual(result["return_scenario"], "none")
        self.assertFalse(result["lifecycle_ambiguous"])

    def test_08_forward_terminated_after_collection_is_ambiguous_return(self):
        result = self._result(state_value="Terminated", collected_from_business_at="2026-08-08 00:00:00")
        self.assertEqual(result["lifecycle_stage"], "terminated")
        self.assertEqual(result["return_scenario"], "ambiguous")
        self.assertTrue(result["lifecycle_ambiguous"])

    def test_09_forward_sparse_data_is_safe_unknown(self):
        result = self._result(state_value=None)
        self.assertEqual(result["lifecycle_stage"], "unknown")

    def test_10_rto_processing_is_returning_to_origin(self):
        result = self._result(flow_code=20, flow_value="Return to Origin", state_value="Processing")
        self.assertEqual(result["lifecycle_stage"], "returning_to_origin")
        self.assertEqual(result["return_scenario"], "pre_delivery_return")

    def test_11_rto_delivered_is_returned_to_origin(self):
        result = self._result(flow_code=20, flow_value="Return to Origin", state_value="Delivered")
        self.assertEqual(result["lifecycle_stage"], "returned_to_origin")
        self.assertNotEqual(result["lifecycle_stage"], "delivered_to_customer")

    def test_12_rto_code_46_is_completion_in_rto_context(self):
        result = self._result(flow_code=20, flow_value="Return to Origin", state_code=46, state_value="Processing")
        self.assertEqual(result["lifecycle_stage"], "returned_to_origin")

    def test_13_rto_always_keeps_pre_delivery_return_scenario(self):
        result = self._result(flow_code=20, flow_value="Return to Origin", state_value="Future State")
        self.assertEqual(result["return_scenario"], "pre_delivery_return")

    def test_14_customer_return_processing_is_pickup(self):
        result = self._result(flow_code=25, flow_value="Customer Return Pickup", state_value="Processing")
        self.assertEqual(result["lifecycle_stage"], "customer_return_pickup")
        self.assertEqual(result["return_scenario"], "post_delivery_customer_return")

    def test_15_customer_return_delivered_is_completed_not_customer_delivery(self):
        result = self._result(flow_code=25, flow_value="Customer Return Pickup", state_value="Delivered")
        self.assertEqual(result["lifecycle_stage"], "customer_return_completed")
        self.assertNotEqual(result["lifecycle_stage"], "delivered_to_customer")

    def test_16_customer_return_terminated_is_not_completed(self):
        result = self._result(flow_code=25, flow_value="Customer Return Pickup", state_value="Terminated")
        self.assertEqual(result["lifecycle_stage"], "terminated")
        self.assertEqual(result["return_scenario"], "post_delivery_customer_return")

    def test_17_done_false_out_for_return_does_nothing(self):
        result = self._result(state_value="Processing", timeline=[{"value": "out_for_return", "done": False}])
        self.assertEqual(result["lifecycle_stage"], "pre_pickup")

    def test_18_done_true_out_for_return_starts_reverse_flow(self):
        result = self._result(state_value="Processing", timeline=[{"value": "out_for_return", "done": True}])
        self.assertEqual(result["lifecycle_stage"], "returning_to_origin")
        self.assertEqual(result["return_scenario"], "pre_delivery_return")

    def test_19_done_true_returned_to_origin_completes_reverse_flow(self):
        result = self._result(state_value="Processing", timeline=[{"value": "returned_to_origin", "done": True}])
        self.assertEqual(result["lifecycle_stage"], "returned_to_origin")

    def test_20_completed_returned_to_origin_beats_processing(self):
        result = self._result(
            flow_code=20,
            flow_value="Return to Origin",
            state_value="Processing",
            timeline=[{"value": "returned_to_origin", "done": True}],
        )
        self.assertEqual(result["lifecycle_rule_code"], "timeline_returned_to_origin")

    def test_21_timeline_none_creates_no_fake_event(self):
        result = self._result(state_value="Processing", timeline=None)
        self.assertEqual(result["lifecycle_stage"], "pre_pickup")

    def test_22_timeline_empty_creates_no_fake_event(self):
        result = self._result(state_value="Processing", timeline=[])
        self.assertEqual(result["lifecycle_stage"], "pre_pickup")

    def test_23_other_delivered_is_ambiguous_not_customer_delivery(self):
        result = self._result(flow_code=999, flow_value="Future Flow", state_value="Delivered")
        self.assertEqual(result["lifecycle_stage"], "ambiguous")
        self.assertTrue(result["lifecycle_ambiguous"])
        self.assertNotEqual(result["lifecycle_stage"], "delivered_to_customer")

    def test_24_unknown_state_does_not_crash(self):
        result = self._result(state_value="Quantum Transit")
        self.assertEqual(result["lifecycle_stage"], "unknown")
        self.assertTrue(result["lifecycle_ambiguous"])

    def test_25_unknown_state_code_does_not_crash(self):
        result = self._result(state_code=999999, state_value="Processing")
        self.assertEqual(result["lifecycle_stage"], "pre_pickup")

    def test_26_unknown_flow_does_not_crash(self):
        result = self._result(flow_code=999999, flow_value="Teleport", state_value="Processing")
        self.assertEqual(result["lifecycle_stage"], "unknown")
        self.assertTrue(result["lifecycle_ambiguous"])

    def test_27_explicit_lost_is_lost(self):
        result = self._result(state_value="Lost")
        self.assertEqual((result["lifecycle_stage"], result["return_scenario"]), ("lost", "lost"))

    def test_28_explicit_damaged_is_damaged(self):
        result = self._result(state_value="Package Damaged")
        self.assertEqual((result["lifecycle_stage"], result["return_scenario"]), ("damaged", "damaged"))

    def test_29_delay_alone_does_not_mean_lost(self):
        result = self._result(state_value="Processing", attempts_count=99)
        self.assertNotIn(result["lifecycle_stage"], {"lost", "damaged"})

    def test_30_rto_alone_does_not_mean_damaged(self):
        result = self._result(flow_code=20, flow_value="Return to Origin", state_value="Processing")
        self.assertNotEqual(result["lifecycle_stage"], "damaged")

    def test_31_cod_zero_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Delivered")
        changed = self._result(state_value="Delivered", cod_amount=0.0)
        self.assertEqual(changed, baseline)

    def test_32_cod_positive_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Processing")
        changed = self._result(state_value="Processing", cod_amount=1000.0)
        self.assertEqual(changed, baseline)

    def test_33_pricing_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Picked Up")
        changed = self._result(state_value="Picked Up", shipping_fee=83.0, price_after_vat=100.0)
        self.assertEqual(changed, baseline)

    def test_34_business_reference_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Processing")
        changed = self._result(state_value="Processing", business_reference="ref-1")
        self.assertEqual(changed, baseline)

    def test_35_unique_business_reference_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Processing")
        changed = self._result(state_value="Processing", unique_business_reference="unique-ref")
        self.assertEqual(changed, baseline)

    def test_36_receiver_does_not_affect_lifecycle(self):
        baseline = self._result(state_value="Processing")
        changed = self._result(state_value="Processing", receiver_name="Synthetic", receiver_phone="01000000000")
        self.assertEqual(changed, baseline)

    def test_37_partial_return_is_never_invented(self):
        cases = [
            self._result(state_value="Delivered"),
            self._result(flow_code=20, flow_value="Return to Origin", state_value="Delivered"),
            self._result(flow_code=25, flow_value="Customer Return Pickup", state_value="Delivered"),
        ]
        self.assertTrue(all(result["return_scenario"] != "partial_return" for result in cases))

    def test_38_interpreter_does_not_mutate_normalized_input(self):
        normalized = self._normalized(
            state_value="Processing",
            timeline=[{"value": "out_for_return", "done": True}],
            cod_amount=100,
        )
        before = deepcopy(normalized)
        self.interpreter.interpret(normalized)
        self.assertEqual(normalized, before)

    def test_39_malformed_optional_timeline_fails_safely(self):
        normalized = self._normalized(state_value="Processing", timeline="bad-timeline")
        result = self.interpreter.interpret(normalized)
        self.assertEqual(result["lifecycle_stage"], "pre_pickup")

    def test_40_malformed_envelope_fails_safely(self):
        result = self.interpreter.interpret(None)
        self.assertEqual(result["lifecycle_stage"], "unknown")
        self.assertTrue(result["lifecycle_ambiguous"])

    def test_41_rule_code_is_fixed_and_contains_no_pii(self):
        normalized = self._normalized(
            state_value="Terminated",
            receiver_name="Private Name",
            receiver_phone="01012345678",
        )
        result = self.interpreter.interpret(normalized)
        rendered = result["lifecycle_rule_code"]
        self.assertNotIn("Private Name", rendered)
        self.assertNotIn("01012345678", rendered)

    def test_42_future_flow_type_alias_rto_is_supported(self):
        normalized = self._normalized(flow_code=None, flow_value=None, state_value="Processing")
        normalized["values"]["flow_type"] = "rto"
        result = self.interpreter.interpret(normalized)
        self.assertEqual(result["lifecycle_stage"], "returning_to_origin")

    def test_43_existing_orm_flow_alias_return_to_origin_is_supported(self):
        normalized = self._normalized(flow_code=None, flow_value=None, state_value="Delivered")
        normalized["values"]["flow_type"] = "return_to_origin"
        result = self.interpreter.interpret(normalized)
        self.assertEqual(result["lifecycle_stage"], "returned_to_origin")

    def test_44_done_false_lost_timeline_is_not_explicit_lost(self):
        result = self._result(state_value="Processing", timeline=[{"value": "Lost", "done": False}])
        self.assertNotEqual(result["lifecycle_stage"], "lost")

    def test_45_not_damaged_text_is_not_classified_as_damaged(self):
        result = self._result(state_value="Not Damaged")
        self.assertNotEqual(result["lifecycle_stage"], "damaged")
