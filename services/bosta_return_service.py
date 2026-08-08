"""Phase 8 safe return linking and exactly-once stock restoration.

The service never auto-links by business reference or PII.  Restoration is
anchored only to Phase 7 inventory snapshots from the safely linked original
forward delivery.
"""

from collections import defaultdict

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError


_RETURN_ENGINE_CONTEXT = "bosta_return_engine"
_EPSILON = 1e-9
_REVIEW_STAGES = {"lost", "damaged", "terminated", "ambiguous"}


class BostaReturnStockBlocked(Exception):
    """Expected stock reservation/availability block during restoration."""


class BostaReturnService:
    def __init__(self, env, config):
        config.ensure_one()
        self.env = env
        self.config = config
        self.company = config.company_id
        allowed = list(env.context.get("allowed_company_ids") or env.user.company_ids.ids)
        if self.company.id not in allowed:
            allowed.append(self.company.id)
        self.ctx = dict(
            env.context,
            allowed_company_ids=allowed,
            **{_RETURN_ENGINE_CONTEXT: True},
        )
        self.Case = env["bosta.return.case"].sudo().with_context(self.ctx).with_company(self.company)
        self.CaseLine = env["bosta.return.case.line"].sudo().with_context(self.ctx).with_company(self.company)
        self.Restoration = env["bosta.return.restoration.effect"].sudo().with_context(self.ctx).with_company(self.company)
        self.RestorationLine = env["bosta.return.restoration.effect.line"].sudo().with_context(self.ctx).with_company(self.company)
        self.InventoryEffect = env["bosta.inventory.effect"].sudo().with_company(self.company)

    @staticmethod
    def _return_type(delivery):
        if delivery.flow_type == "return_to_origin":
            return "pre_delivery_return"
        if delivery.flow_type == "customer_return":
            return "post_delivery_customer_return"
        return False

    def _set_case(self, case, state, reason_code=False, **extra):
        vals = {"state": state, "reason_code": reason_code or False, **extra}
        case.with_context(self.ctx).write(vals)
        return case

    def _case_for_delivery(self, delivery):
        return_type = self._return_type(delivery)
        if not return_type:
            return False
        case = self.Case.search([
            ("company_id", "=", self.company.id),
            ("return_delivery_id", "=", delivery.id),
        ], limit=1)
        if not case:
            case = self.Case.create({
                "company_id": self.company.id,
                "return_delivery_id": delivery.id,
                "original_delivery_id": delivery.original_delivery_id.id or False,
                "return_type": return_type,
                "inspection_state": "pending" if return_type == "post_delivery_customer_return" else "not_required",
                "state": "pending_link",
                "reason_code": "missing_original_link" if not delivery.original_delivery_id else False,
            })
            return case

        # Flow identity is deterministic and cannot be manager-edited. If old
        # data somehow conflicts, keep it blocked rather than silently rewrite.
        if case.return_type != return_type:
            return self._set_case(case, "blocked", "return_type_conflict")

        delivery_original = delivery.original_delivery_id
        if delivery_original and not case.original_delivery_id:
            case.with_context(self.ctx).write({"original_delivery_id": delivery_original.id})
        elif delivery_original and case.original_delivery_id != delivery_original:
            return self._set_case(case, "blocked", "conflicting_original_relation")
        return case

    @staticmethod
    def _validate_link(return_delivery, original):
        if not original:
            return "missing_original_link"
        if return_delivery == original:
            return "self_link_rejected"
        if return_delivery.company_id != original.company_id:
            return "cross_company_link_rejected"
        if original.flow_type != "forward":
            return "original_not_forward"
        if return_delivery.flow_type not in ("return_to_origin", "customer_return"):
            return "return_flow_invalid"
        return False

    def link_original(self, case, original):
        case.ensure_one()
        original.ensure_one()
        if case.company_id != self.company:
            raise ValidationError("Bosta return case/config company mismatch.")
        reason = self._validate_link(case.return_delivery_id, original)
        if reason:
            raise UserError("The selected original delivery is not a safe valid forward link (%s)." % reason)
        existing = case.return_delivery_id.original_delivery_id
        if existing and existing != original:
            raise UserError("This return is already linked to a different original delivery. Unlink it first after review.")
        if case.original_delivery_id and case.original_delivery_id != original:
            raise UserError("The return case already contains a conflicting original relation. Unlink it first.")
        if case.restoration_effect_id and case.restoration_effect_id.status == "applied":
            raise UserError("A restored return cannot be relinked.")

        case.return_delivery_id.sudo().with_company(self.company).write({"original_delivery_id": original.id})
        case.with_context(self.ctx).write({
            "original_delivery_id": original.id,
            "link_candidate_delivery_id": original.id,
            "reason_code": False,
        })
        self._sync_customer_case_lines(case)
        return case

    def unlink_original(self, case):
        case.ensure_one()
        if case.restoration_effect_id and case.restoration_effect_id.status == "applied":
            raise UserError("A restored return cannot be unlinked from its historical original delivery.")
        effect = self.Restoration.search([("return_case_id", "=", case.id)], limit=1)
        if effect:
            effect.line_ids.unlink()
            case.with_context(self.ctx).write({"restoration_effect_id": False})
            effect.unlink()
        case.return_line_ids.with_context(self.ctx).unlink()
        case.return_delivery_id.sudo().with_company(self.company).write({"original_delivery_id": False})
        case.with_context(self.ctx).write({
            "original_delivery_id": False,
            "link_candidate_delivery_id": False,
            "state": "pending_link",
            "reason_code": "missing_original_link",
            "inspection_state": "pending" if case.return_type == "post_delivery_customer_return" else "not_required",
            "restored_at": False,
        })
        return case

    def _original_effect(self, case):
        if not case.original_delivery_id:
            return self.InventoryEffect.browse()
        return self.InventoryEffect.search([
            ("company_id", "=", self.company.id),
            ("delivery_id", "=", case.original_delivery_id.id),
        ], limit=1)

    def _customer_evidence_reason(self, case, effect):
        original = case.original_delivery_id
        if not effect or not effect.outbound_picking_id:
            return "missing_original_outbound_effect"
        if not effect.final_picking_id or effect.final_picking_id.state != "done":
            return "original_not_delivered_in_inventory"
        if original.lifecycle_stage != "delivered_to_customer":
            return "original_lifecycle_not_delivered"
        if not effect.source_location_id:
            return "missing_original_source_snapshot"
        customer_location = effect.final_picking_id.location_dest_id
        if not customer_location or customer_location.usage != "customer":
            return "original_customer_location_not_proven"
        if not effect.line_ids:
            return "missing_original_inventory_lines"
        return False

    def _sync_customer_case_lines(self, case):
        if case.return_type != "post_delivery_customer_return" or not case.original_delivery_id:
            return
        if case.restoration_effect_id and case.restoration_effect_id.status == "applied":
            return
        effect = self._original_effect(case)
        if self._customer_evidence_reason(case, effect):
            return
        if case.return_line_ids:
            return
        values = []
        for line in effect.line_ids:
            if line.main_quantity <= _EPSILON:
                continue
            values.append({
                "case_id": case.id,
                "original_inventory_effect_line_id": line.id,
                "product_id": line.main_product_id.id,
                "max_delivered_quantity": line.main_quantity,
                "returned_quantity": 0,
            })
        if values:
            self.CaseLine.create(values)

    def _lock_original_lines(self, lines):
        ids = sorted(set(lines.ids))
        if not ids:
            return
        self.env.cr.execute(
            "SELECT id FROM bosta_inventory_effect_line WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (ids,),
        )

    def _restored_quantity(self, original_line, role):
        rows = self.RestorationLine.search([
            ("original_inventory_effect_line_id", "=", original_line.id),
            ("role", "=", role),
            ("effect_id.status", "=", "applied"),
        ])
        return sum(rows.mapped("quantity"))

    def _prepare_rto_lines(self, case):
        effect = self._original_effect(case)
        if not effect or not effect.outbound_picking_id or effect.outbound_picking_id.state != "done":
            return False, "missing_original_outbound_effect"
        if effect.final_picking_id:
            return False, "pre_delivery_return_original_already_delivered"
        if not effect.source_location_id or not effect.transit_location_id:
            return False, "missing_original_location_snapshot"
        if not effect.line_ids:
            return False, "missing_original_inventory_lines"

        self._lock_original_lines(effect.line_ids)
        prepared = []
        for line in effect.line_ids:
            if line.main_quantity > _EPSILON:
                if self._restored_quantity(line, "main") > _EPSILON:
                    return False, "original_main_already_restored"
                prepared.append({
                    "original_line": line,
                    "product": line.main_product_id,
                    "role": "main",
                    "quantity": line.main_quantity,
                    "source": effect.transit_location_id,
                    "destination": effect.source_location_id,
                })
            if line.tester_product_id and line.tester_quantity > _EPSILON:
                if self._restored_quantity(line, "tester") > _EPSILON:
                    return False, "original_tester_already_restored"
                prepared.append({
                    "original_line": line,
                    "product": line.tester_product_id,
                    "role": "tester",
                    "quantity": line.tester_quantity,
                    "source": effect.transit_location_id,
                    "destination": effect.source_location_id,
                })
        if not prepared:
            return False, "no_original_outbound_quantity"
        return prepared, False

    def _prepare_customer_lines(self, case):
        effect = self._original_effect(case)
        reason = self._customer_evidence_reason(case, effect)
        if reason:
            return False, reason
        self._sync_customer_case_lines(case)
        positive = case.return_line_ids.filtered(lambda line: line.returned_quantity > _EPSILON)
        if not positive:
            return False, "returned_quantity_required"

        original_lines = positive.mapped("original_inventory_effect_line_id")
        self._lock_original_lines(original_lines)
        customer = effect.final_picking_id.location_dest_id
        prepared = []
        for case_line in positive:
            original_line = case_line.original_inventory_effect_line_id
            quantity = case_line.returned_quantity
            if quantity > original_line.main_quantity + _EPSILON:
                return False, "returned_quantity_exceeds_original"
            restored = self._restored_quantity(original_line, "main")
            remaining = max(0.0, original_line.main_quantity - restored)
            if quantity > remaining + _EPSILON:
                return False, "cumulative_over_restore_blocked"
            prepared.append({
                "original_line": original_line,
                "product": original_line.main_product_id,
                "role": "main",
                "quantity": quantity,
                "source": customer,
                "destination": effect.source_location_id,
            })
        return prepared, False

    def _restoration_effect(self, case):
        effect = self.Restoration.search([
            ("company_id", "=", self.company.id),
            ("return_case_id", "=", case.id),
        ], limit=1)
        if not effect:
            effect = self.Restoration.create({
                "company_id": self.company.id,
                "return_case_id": case.id,
                "return_delivery_id": case.return_delivery_id.id,
                "original_delivery_id": case.original_delivery_id.id,
                "return_type": case.return_type,
                "status": "pending",
            })
        if case.restoration_effect_id != effect:
            case.with_context(self.ctx).write({"restoration_effect_id": effect.id})
        return effect

    @staticmethod
    def _aggregate(prepared):
        grouped = defaultdict(float)
        for row in prepared:
            grouped[row["product"]] += row["quantity"]
        return grouped

    def _rto_picking_type(self, case):
        original_effect = self._original_effect(case)
        if original_effect.outbound_picking_id:
            return original_effect.outbound_picking_id.picking_type_id
        return self.env["stock.picking.type"].browse()

    def _customer_return_picking_type(self, case):
        original_effect = self._original_effect(case)
        for picking in (original_effect.final_picking_id, original_effect.outbound_picking_id):
            if picking and picking.picking_type_id.warehouse_id and picking.picking_type_id.warehouse_id.in_type_id:
                return picking.picking_type_id.warehouse_id.in_type_id
        PickingType = self.env["stock.picking.type"].sudo().with_company(self.company)
        types = PickingType.search([
            ("company_id", "=", self.company.id),
            ("code", "=", "incoming"),
        ])
        return types if len(types) == 1 else PickingType.browse()

    def _create_done_picking(self, case, prepared, picking_type):
        source = prepared[0]["source"]
        destination = prepared[0]["destination"]
        if any(row["source"] != source or row["destination"] != destination for row in prepared):
            raise RuntimeError("A single return restoration cannot span conflicting historical locations.")
        requirements = self._aggregate(prepared)
        Picking = self.env["stock.picking"].sudo().with_context(self.ctx).with_company(self.company)
        moves = [
            Command.create({
                "name": "%s - %s" % (case.return_delivery_id.tracking_number, product.display_name),
                "product_id": product.id,
                "product_uom_qty": quantity,
                "product_uom": product.uom_id.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "company_id": self.company.id,
            })
            for product, quantity in requirements.items()
        ]
        picking = Picking.create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "company_id": self.company.id,
            "origin": "BOSTA/%s/RETURN-%s" % (
                case.return_delivery_id.tracking_number,
                "RTO" if case.return_type == "pre_delivery_return" else "CUSTOMER",
            ),
            "move_ids": moves,
        })
        picking.action_confirm()
        picking.action_assign()
        if source.usage in ("internal", "transit") and picking.state != "assigned":
            raise BostaReturnStockBlocked("Historical return stock is no longer fully reservable at the expected source location.")
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        if picking.state != "done":
            raise RuntimeError("Bosta return restoration picking did not validate to Done state.")
        return picking

    def _apply_restoration(self, case, prepared, rule_code):
        effect = self._restoration_effect(case)
        if effect.status == "applied":
            return self._set_case(
                case,
                "restored",
                effect.rule_code or rule_code,
                restored_at=effect.applied_at,
                restoration_effect_id=effect.id,
            )

        effect.line_ids.unlink()
        self.RestorationLine.create([
            {
                "effect_id": effect.id,
                "product_id": row["product"].id,
                "role": row["role"],
                "quantity": row["quantity"],
                "source_location_id": row["source"].id,
                "destination_location_id": row["destination"].id,
                "original_inventory_effect_line_id": row["original_line"].id,
            }
            for row in prepared
        ])

        picking_type = (
            self._rto_picking_type(case)
            if case.return_type == "pre_delivery_return"
            else self._customer_return_picking_type(case)
        )
        if not picking_type:
            effect.with_context(self.ctx).write({"status": "blocked", "rule_code": "missing_restoration_picking_type"})
            return self._set_case(case, "blocked", "missing_restoration_picking_type")

        try:
            with self.env.cr.savepoint():
                picking = self._create_done_picking(case, prepared, picking_type)
        except BostaReturnStockBlocked:
            effect.with_context(self.ctx).write({"status": "blocked", "rule_code": "restoration_stock_unavailable"})
            return self._set_case(case, "blocked", "restoration_stock_unavailable")

        now = fields.Datetime.now()
        effect.with_context(self.ctx).write({
            "status": "applied",
            "rule_code": rule_code,
            "picking_id": picking.id,
            "applied_at": now,
        })
        return self._set_case(
            case,
            "restored",
            rule_code,
            restored_at=now,
            restoration_effect_id=effect.id,
        )

    def process_case(self, case):
        case.ensure_one()
        if case.company_id != self.company:
            raise ValueError("Bosta return case/config company mismatch.")
        delivery = case.return_delivery_id

        # Applied restoration is final/idempotent.  A stale or contradictory
        # lifecycle payload must never regress the audit case or reverse stock.
        if case.restoration_effect_id and case.restoration_effect_id.status == "applied":
            return self._set_case(
                case,
                "restored",
                case.restoration_effect_id.rule_code,
                restored_at=case.restoration_effect_id.applied_at,
            )

        # Never restore terminal uncertainty automatically.
        if delivery.lifecycle_stage in _REVIEW_STAGES or delivery.return_scenario in {"lost", "damaged", "ambiguous"}:
            return self._set_case(case, "review_required", "terminal_return_review_required")

        # bosta.delivery.original_delivery_id remains the authoritative relation.
        # Never restore from a stale case-only link if that field was cleared.
        original = delivery.original_delivery_id
        if not original:
            if case.original_delivery_id:
                return self._set_case(case, "blocked", "authoritative_original_link_missing")
            return self._set_case(case, "pending_link", "missing_original_link")
        reason = self._validate_link(delivery, original)
        if reason:
            return self._set_case(case, "blocked", reason)
        if case.original_delivery_id and case.original_delivery_id != original:
            return self._set_case(case, "blocked", "conflicting_original_relation")
        if not case.original_delivery_id:
            case.with_context(self.ctx).write({"original_delivery_id": original.id})

        if not self.config.inventory_sync_enabled:
            return self._set_case(case, "blocked", "inventory_sync_disabled")

        if case.return_type == "pre_delivery_return":
            if delivery.return_scenario != "pre_delivery_return":
                return self._set_case(case, "review_required", "rto_scenario_not_authoritative")
            if delivery.lifecycle_stage == "returning_to_origin":
                return self._set_case(case, "awaiting_physical_return", "awaiting_return_to_origin_completion")
            if delivery.lifecycle_stage != "returned_to_origin":
                return self._set_case(case, "review_required", "rto_lifecycle_not_restorable")
            prepared, reason = self._prepare_rto_lines(case)
            if reason:
                return self._set_case(case, "blocked", reason)
            self._set_case(case, "ready_to_restore", "rto_ready")
            return self._apply_restoration(case, prepared, "rto_returned_to_original_source")

        if delivery.return_scenario != "post_delivery_customer_return":
            return self._set_case(case, "review_required", "customer_return_scenario_not_authoritative")
        if delivery.lifecycle_stage == "customer_return_pickup":
            self._sync_customer_case_lines(case)
            return self._set_case(case, "awaiting_physical_return", "awaiting_customer_return_completion")
        if delivery.lifecycle_stage != "customer_return_completed":
            return self._set_case(case, "review_required", "customer_return_lifecycle_not_restorable")

        evidence_reason = self._customer_evidence_reason(case, self._original_effect(case))
        if evidence_reason:
            return self._set_case(case, "blocked", evidence_reason)
        self._sync_customer_case_lines(case)
        if case.inspection_state == "rejected":
            return self._set_case(case, "rejected", "inspection_rejected")
        if case.inspection_state != "accepted":
            return self._set_case(case, "awaiting_inspection", "inspection_pending")
        prepared, reason = self._prepare_customer_lines(case)
        if reason:
            return self._set_case(case, "blocked", reason)
        self._set_case(case, "ready_to_restore", "customer_return_ready")
        return self._apply_restoration(case, prepared, "customer_return_main_inspection_accepted")

    def process_delivery(self, delivery):
        delivery.ensure_one()
        if delivery.company_id != self.company:
            raise ValueError("Bosta delivery/config company mismatch.")
        case = self._case_for_delivery(delivery)
        if not case:
            return False
        return self.process_case(case)

    def accept_customer_return(self, case):
        case.ensure_one()
        if case.return_type != "post_delivery_customer_return":
            raise UserError("Inspection acceptance applies only to post-delivery customer returns.")
        if case.state == "restored":
            return case
        if case.inspection_state == "rejected":
            raise UserError("A rejected return inspection cannot later be accepted without a separate reviewed workflow.")
        # First refresh link/evidence/quantity rows without changing inspection.
        self.process_case(case)
        if not case.original_delivery_id:
            return self._set_case(case, "pending_link", "missing_original_link")
        if case.return_delivery_id.lifecycle_stage != "customer_return_completed":
            return self._set_case(case, "awaiting_physical_return", "customer_return_not_completed")
        effect = self._original_effect(case)
        reason = self._customer_evidence_reason(case, effect)
        if reason:
            return self._set_case(case, "blocked", reason)
        self._sync_customer_case_lines(case)
        if not any(line.returned_quantity > _EPSILON for line in case.return_line_ids):
            return self._set_case(case, "awaiting_inspection", "returned_quantity_required")
        case.with_context(self.ctx).write({"inspection_state": "accepted"})
        return self.process_case(case)

    def reject_customer_return(self, case):
        case.ensure_one()
        if case.return_type != "post_delivery_customer_return":
            raise UserError("Inspection rejection applies only to post-delivery customer returns.")
        if case.state == "restored" or (case.restoration_effect_id and case.restoration_effect_id.status == "applied"):
            raise UserError("A stock-restored return cannot be rejected afterward.")
        if case.inspection_state == "rejected":
            return case
        if case.inspection_state == "accepted":
            raise UserError("An accepted inspection cannot be rejected by the simple return action.")
        case.with_context(self.ctx).write({
            "inspection_state": "rejected",
            "state": "rejected",
            "reason_code": "inspection_rejected",
        })
        return case
