"""Pure Phase 6 Bosta lifecycle interpretation.

The interpreter consumes the Phase 4 normalized delivery envelope and returns
only derived lifecycle facts. It has no ORM, HTTP, credentials, stock, sales,
product/customer mapping, financial, scheduling, or persistence behavior.
"""

import re


LIFECYCLE_STAGES = (
    "unknown",
    "pre_pickup",
    "with_bosta",
    "delivered_to_customer",
    "returning_to_origin",
    "returned_to_origin",
    "customer_return_pickup",
    "customer_return_completed",
    "terminated",
    "lost",
    "damaged",
    "ambiguous",
)

RETURN_SCENARIOS = (
    "none",
    "pre_delivery_return",
    "post_delivery_customer_return",
    "partial_return",
    "lost",
    "damaged",
    "ambiguous",
)


class BostaLifecycleInterpreter:
    """Conservatively derive stable lifecycle facts from normalized Bosta data."""

    _FORWARD = "forward"
    _RTO = "rto"
    _CUSTOMER_RETURN = "customer_return"
    _OTHER = "other"

    _DELIVERED_CODES = {45}
    _RTO_COMPLETION_CODES = {46}
    _PICKED_UP_CODES = {21}

    @staticmethod
    def _text(value):
        if not isinstance(value, str):
            return ""
        return value.strip().casefold()

    @classmethod
    def _token(cls, value):
        text = cls._text(value)
        if not text:
            return ""
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

    @classmethod
    def _flow_type(cls, values):
        # Accept a future normalized flow_type if present, but remain compatible
        # with the current Phase 4 envelope where flow is derived from raw type.
        flow = cls._token(values.get("flow_type"))
        if flow in {"forward", "send"}:
            return cls._FORWARD
        if flow in {"rto", "return_to_origin", "return_origin"}:
            return cls._RTO
        if flow in {"customer_return", "customer_return_pickup"}:
            return cls._CUSTOMER_RETURN
        if flow and flow != "other":
            return cls._OTHER

        code = values.get("delivery_type_code")
        if not isinstance(code, bool):
            if code == 10:
                return cls._FORWARD
            if code == 20:
                return cls._RTO
            if code == 25:
                return cls._CUSTOMER_RETURN

        raw_type = cls._token(values.get("delivery_type_value"))
        if raw_type == "send":
            return cls._FORWARD
        if raw_type == "return_to_origin":
            return cls._RTO
        if raw_type == "customer_return_pickup":
            return cls._CUSTOMER_RETURN
        return cls._OTHER

    @classmethod
    def _state_token(cls, values):
        return cls._token(values.get("state_value"))

    @staticmethod
    def _state_code(values):
        code = values.get("state_code")
        if isinstance(code, bool) or not isinstance(code, int):
            return None
        return code

    @staticmethod
    def _has_left_business(values):
        return any(
            values.get(field) not in (None, False, "")
            for field in ("collected_from_business_at", "picked_up_at")
        )

    @classmethod
    def _explicit_problem(cls, values, timeline):
        """Return lost/damaged only for explicit textual lifecycle evidence."""
        texts = [
            values.get("state_value"),
            values.get("state_child_state"),
            values.get("masked_state"),
        ]
        if isinstance(timeline, list):
            texts.extend(
                event.get("value")
                for event in timeline
                if isinstance(event, dict) and event.get("done") is True
            )

        for text in texts:
            normalized = cls._text(text)
            if not normalized:
                continue
            token = cls._token(normalized)
            if token.startswith(("not_", "no_")):
                continue
            if re.search(r"\bdamaged\b", normalized):
                return "damaged"
            if re.search(r"\blost\b", normalized):
                return "lost"
        return None

    @classmethod
    def _timeline_facts(cls, timeline):
        facts = {
            "out_for_return": False,
            "returned_to_origin": False,
        }
        if not isinstance(timeline, list):
            return facts
        for event in timeline:
            if not isinstance(event, dict) or event.get("done") is not True:
                continue
            token = cls._token(event.get("value"))
            if token in {
                "returned_to_origin",
                "returned_to_sender",
                "return_to_origin_completed",
                "return_to_origin_complete",
            }:
                facts["returned_to_origin"] = True
            elif token in {
                "out_for_return",
                "out_for_return_to_origin",
                "returning_to_origin",
            }:
                facts["out_for_return"] = True
        return facts

    @staticmethod
    def _result(stage, scenario, rule_code, ambiguous=False):
        return {
            "lifecycle_stage": stage,
            "return_scenario": scenario,
            "lifecycle_rule_code": rule_code,
            "lifecycle_ambiguous": bool(ambiguous),
        }

    def interpret(self, normalized):
        """Return derived lifecycle facts without mutating *normalized*.

        Malformed optional lifecycle data is intentionally treated
        conservatively rather than propagated as an exception.
        """
        if not isinstance(normalized, dict):
            return self._result("unknown", "none", "malformed_input", True)
        values = normalized.get("values")
        if not isinstance(values, dict):
            return self._result("unknown", "none", "malformed_values", True)

        timeline = normalized.get("timeline")
        flow = self._flow_type(values)
        state = self._state_token(values)
        code = self._state_code(values)
        left_business = self._has_left_business(values)

        # Highest-precedence explicit terminal problem facts.
        problem = self._explicit_problem(values, timeline)
        if problem == "damaged":
            return self._result("damaged", "damaged", "explicit_damaged")
        if problem == "lost":
            return self._result("lost", "lost", "explicit_lost")

        timeline_facts = self._timeline_facts(timeline)

        # A completed returned-to-origin event is stronger than a weak current
        # transient state and is intentionally allowed to enrich Details data.
        if timeline_facts["returned_to_origin"]:
            return self._result(
                "returned_to_origin",
                "pre_delivery_return",
                "timeline_returned_to_origin",
            )

        if flow == self._RTO:
            if state == "delivered" or code in self._RTO_COMPLETION_CODES:
                return self._result(
                    "returned_to_origin",
                    "pre_delivery_return",
                    "rto_returned",
                )
            if state == "terminated":
                return self._result(
                    "terminated",
                    "pre_delivery_return",
                    "rto_terminated",
                )
            if state in {
                "processing",
                "picked_up",
                "out_for_return",
                "returning_to_origin",
                "return_to_origin",
            } or timeline_facts["out_for_return"]:
                rule = "timeline_out_for_return" if timeline_facts["out_for_return"] else "rto_processing"
                return self._result(
                    "returning_to_origin",
                    "pre_delivery_return",
                    rule,
                )
            return self._result(
                "unknown",
                "pre_delivery_return",
                "rto_unknown_state",
                True,
            )

        if flow == self._FORWARD:
            # Delivered must be interpreted only in forward-flow context.
            if state == "delivered" or code in self._DELIVERED_CODES:
                return self._result(
                    "delivered_to_customer",
                    "none",
                    "forward_delivered",
                )

            if timeline_facts["out_for_return"]:
                return self._result(
                    "returning_to_origin",
                    "pre_delivery_return",
                    "timeline_out_for_return",
                )

            if state == "terminated":
                if left_business:
                    return self._result(
                        "terminated",
                        "ambiguous",
                        "forward_terminated_after_pickup",
                        True,
                    )
                return self._result(
                    "terminated",
                    "none",
                    "forward_terminated_pre_pickup",
                )

            if state == "picked_up" or code in self._PICKED_UP_CODES:
                return self._result(
                    "with_bosta",
                    "none",
                    "forward_picked_up",
                )

            if left_business:
                return self._result(
                    "with_bosta",
                    "none",
                    "forward_collected",
                )

            if state in {
                "processing",
                "pending",
                "pending_pickup",
                "new",
                "created",
            } or values.get("pending_pickup_at") not in (None, False, ""):
                return self._result(
                    "pre_pickup",
                    "none",
                    "forward_pre_pickup",
                )

            if not state and code is None:
                return self._result("unknown", "none", "forward_sparse", False)
            return self._result("unknown", "none", "forward_unknown_state", True)

        if flow == self._CUSTOMER_RETURN:
            if state == "delivered":
                return self._result(
                    "customer_return_completed",
                    "post_delivery_customer_return",
                    "customer_return_completed",
                )
            if state == "terminated":
                return self._result(
                    "terminated",
                    "post_delivery_customer_return",
                    "customer_return_terminated",
                )
            if state in {
                "processing",
                "picked_up",
                "pending",
                "pending_pickup",
                "out_for_pickup",
                "pickup",
            } or code in self._PICKED_UP_CODES or left_business:
                return self._result(
                    "customer_return_pickup",
                    "post_delivery_customer_return",
                    "customer_return_processing",
                )
            return self._result(
                "unknown",
                "post_delivery_customer_return",
                "customer_return_unknown_state",
                True,
            )

        # Unknown/future flow values stay conservative. A generic Delivered
        # state must never be treated as customer delivery here.
        if state == "delivered":
            scenario = "ambiguous" if timeline_facts["out_for_return"] else "none"
            return self._result(
                "ambiguous",
                scenario,
                "ambiguous_delivered",
                True,
            )
        if timeline_facts["out_for_return"]:
            return self._result(
                "ambiguous",
                "ambiguous",
                "unknown_flow_reverse_evidence",
                True,
            )
        return self._result("unknown", "none", "unknown_flow", True)
