"""Phase 7 idempotent outbound inventory effects.

Only forward merchandise departure is represented here.  Phase 8 return/RTO
restoration is intentionally absent.
"""

from collections import defaultdict

from odoo import Command, fields

from .bosta_product_mapping_service import BostaProductMappingService


_ENGINE_CONTEXT = "bosta_inventory_engine"
_DEPARTED_STAGES = {
    "with_bosta",
    "delivered_to_customer",
    "returning_to_origin",
    "returned_to_origin",
}
_REVIEW_STAGES = {"terminated", "lost", "damaged"}


class BostaInventoryStockBlocked(Exception):
    """Expected stock-availability race or reservation block."""


class BostaInventoryService:
    def __init__(self, env, config):
        config.ensure_one()
        self.env = env
        self.config = config
        self.company = config.company_id
        allowed = list(env.context.get("allowed_company_ids") or env.user.company_ids.ids)
        if self.company.id not in allowed:
            allowed.append(self.company.id)
        self.ctx = dict(env.context, allowed_company_ids=allowed, **{_ENGINE_CONTEXT: True})
        self.Effect = env["bosta.inventory.effect"].sudo().with_context(self.ctx).with_company(self.company)
        self.EffectLine = env["bosta.inventory.effect.line"].sudo().with_context(self.ctx).with_company(self.company)
        self.mapping = BostaProductMappingService(env, self.company)

    def _effect(self, delivery):
        effect = self.Effect.search([
            ("company_id", "=", self.company.id),
            ("delivery_id", "=", delivery.id),
        ], limit=1)
        if not effect:
            effect = self.Effect.create({
                "company_id": self.company.id,
                "delivery_id": delivery.id,
                "source_location_id": self.config.stock_source_location_id.id,
                "transit_location_id": self.config.bosta_transit_location_id.id,
                "status": "pending_departure",
            })
        return effect

    def _set_effect(self, effect, status, reason=False, **extra):
        vals = {"status": status, "blocked_reason": reason or False, **extra}
        effect.write(vals)
        return effect

    @staticmethod
    def _explicit_departure_at(delivery):
        dates = [date for date in (delivery.collected_from_business_at, delivery.picked_up_at) if date]
        return min(dates) if dates else False

    def _eligibility(self, delivery):
        if delivery.flow_type != "forward":
            return "not_applicable", "Reverse/non-forward Bosta records never create outbound stock."
        cutoff = self.config.inventory_effective_from
        departure = self._explicit_departure_at(delivery)
        if departure:
            if departure < cutoff:
                return "not_applicable", "Collection occurred before the inventory go-live cutoff."
            return "eligible", False
        if delivery.lifecycle_stage in _REVIEW_STAGES:
            return "pending_departure", "Strong pickup evidence is required for this terminal exception state."
        if delivery.lifecycle_stage not in _DEPARTED_STAGES:
            return "pending_departure", "No strong evidence that merchandise left the business."
        if not delivery.bosta_created_at or delivery.bosta_created_at < cutoff:
            return "not_applicable", "Historical delivery is outside the inventory go-live cutoff."
        return "eligible", False

    def _snapshot_lines(self, effect, resolution):
        if effect.outbound_picking_id:
            return
        effect.line_ids.unlink()
        values = []
        for row in resolution["lines"]:
            candidate = row["candidate"]
            product = row["product"]
            tester = row["tester"]
            qty = row["quantity"]
            values.append({
                "effect_id": effect.id,
                "mapping_id": row["mapping"].id if row["mapping"] else False,
                "source_external_product_id": candidate.get("external_product_id") or False,
                "source_product_code": candidate.get("source_product_code") or False,
                "source_title": candidate.get("source_title") or False,
                "main_product_id": product.id,
                "tester_product_id": tester.id if tester else False,
                "main_quantity": qty,
                "tester_quantity": qty if tester else 0,
            })
        if values:
            self.EffectLine.create(values)

    @staticmethod
    def _aggregate_requirements(lines):
        required = defaultdict(float)
        for row in lines:
            required[row["product"]] += row["quantity"]
            if row["tester"]:
                required[row["tester"]] += row["quantity"]
        return required

    def _check_stock(self, requirements, location):
        for product, quantity in requirements.items():
            if not product.is_storable:
                return False, "Mapped product %s is not storable." % product.display_name
            if product.tracking != "none":
                return False, "Tracked product %s requires manual lot/serial handling." % product.display_name
            available = product.sudo().with_company(self.company).with_context(location=location.id).free_qty
            if available + 1e-9 < quantity:
                return False, "Insufficient stock for %s." % product.display_name
        return True, False

    def _internal_picking_type(self):
        if self.config.stock_picking_type_id:
            return self.config.stock_picking_type_id
        PickingType = self.env["stock.picking.type"].sudo().with_company(self.company)
        types = PickingType.search([
            ("company_id", "=", self.company.id),
            ("code", "=", "internal"),
        ])
        return types if len(types) == 1 else PickingType.browse()

    def _outgoing_picking_type(self, effect=False):
        # Once outbound exists, prefer the same warehouse context that actually
        # performed that historical transfer. A later config change must not
        # silently switch finalization to another warehouse.
        if effect and effect.outbound_picking_id:
            applied_type = effect.outbound_picking_id.picking_type_id
            if applied_type.warehouse_id and applied_type.warehouse_id.out_type_id:
                return applied_type.warehouse_id.out_type_id

        internal = self._internal_picking_type()
        if internal and internal.warehouse_id and internal.warehouse_id.out_type_id:
            return internal.warehouse_id.out_type_id
        PickingType = self.env["stock.picking.type"].sudo().with_company(self.company)
        types = PickingType.search([
            ("company_id", "=", self.company.id),
            ("code", "=", "outgoing"),
        ])
        return types if len(types) == 1 else PickingType.browse()

    def _create_done_picking(self, delivery, requirements, source, destination, picking_type, suffix):
        Picking = self.env["stock.picking"].sudo().with_context(self.ctx).with_company(self.company)
        move_commands = []
        for product, quantity in requirements.items():
            move_commands.append(Command.create({
                "name": "%s - %s" % (delivery.tracking_number, product.display_name),
                "product_id": product.id,
                "product_uom_qty": quantity,
                "product_uom": product.uom_id.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "company_id": self.company.id,
            }))
        picking = Picking.create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "company_id": self.company.id,
            "origin": "BOSTA/%s/%s" % (delivery.tracking_number, suffix),
            "move_ids": move_commands,
        })
        picking.action_confirm()
        picking.action_assign()
        # The earlier free_qty check is a user-friendly preflight only.  Stock
        # may change before reservation, so require Odoo to fully reserve the
        # whole picking before setting done quantities.  Never force a partial
        # or negative move.
        if picking.state != "assigned":
            raise BostaInventoryStockBlocked("Stock changed before the full Bosta transfer could be reserved.")
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        if picking.state != "done":
            raise RuntimeError("Bosta stock picking did not validate to Done state.")
        return picking

    def process_delivery(self, delivery):
        """Ensure the correct Phase 7 effect for one persisted delivery."""
        delivery.ensure_one()
        if delivery.company_id != self.company:
            raise ValueError("Bosta delivery/config company mismatch.")
        if not self.config.inventory_sync_enabled:
            return False

        effect = self._effect(delivery)
        eligibility, reason = self._eligibility(delivery)
        if eligibility != "eligible":
            # Never downgrade an already applied outbound picking because later
            # lifecycle observations still need the physical audit trail.
            if effect.outbound_picking_id:
                return effect
            return self._set_effect(effect, eligibility, reason)

        # Idempotency: if outbound exists, never create it again.
        if not effect.outbound_picking_id:
            resolution = self.mapping.resolve_delivery(delivery)
            tester_missing = any(row.get("tester_missing") for row in resolution["lines"])
            if not resolution["resolved"]:
                status = "blocked_tester" if tester_missing else "blocked_mapping"
                return self._set_effect(effect, status, resolution["reason"])

            self._snapshot_lines(effect, resolution)
            requirements = self._aggregate_requirements(resolution["lines"])

            # Before outbound is applied, config is allowed to evolve. Refresh
            # the effect audit locations to the exact locations this attempt is
            # about to use. Once outbound_picking_id exists these fields become
            # immutable historical snapshots (also enforced by the model).
            source = self.config.stock_source_location_id
            transit = self.config.bosta_transit_location_id
            effect.write({
                "source_location_id": source.id,
                "transit_location_id": transit.id,
            })

            stock_ok, stock_reason = self._check_stock(requirements, source)
            if not stock_ok:
                return self._set_effect(effect, "blocked_stock", stock_reason)
            picking_type = self._internal_picking_type()
            if not picking_type:
                return self._set_effect(effect, "blocked_stock", "No unique internal stock operation type is configured.")
            self._set_effect(effect, "ready")
            try:
                with self.env.cr.savepoint():
                    outbound = self._create_done_picking(
                        delivery,
                        requirements,
                        effect.source_location_id,
                        effect.transit_location_id,
                        picking_type,
                        "OUTBOUND",
                    )
            except BostaInventoryStockBlocked as exc:
                return self._set_effect(effect, "blocked_stock", str(exc))
            self._set_effect(
                effect,
                "outbound_applied",
                outbound_picking_id=outbound.id,
                outbound_applied_at=fields.Datetime.now(),
            )

        stage = delivery.lifecycle_stage
        if stage == "delivered_to_customer" and not effect.final_picking_id:
            requirements = defaultdict(float)
            for line in effect.line_ids:
                requirements[line.main_product_id] += line.main_quantity
                if line.tester_product_id:
                    requirements[line.tester_product_id] += line.tester_quantity
            # Finalization is anchored to the location snapshot used by the
            # already-applied outbound move, never to mutable current config.
            transit = effect.transit_location_id
            stock_ok, stock_reason = self._check_stock(requirements, transit)
            if not stock_ok:
                return self._set_effect(effect, "blocked_stock", stock_reason)
            outgoing_type = self._outgoing_picking_type(effect)
            if not outgoing_type:
                return self._set_effect(effect, "exception", "No unique outgoing stock operation type is available for delivery finalization.")
            customer = self.env.ref("stock.stock_location_customers").sudo()
            try:
                with self.env.cr.savepoint():
                    final = self._create_done_picking(
                        delivery, requirements, transit,
                        customer, outgoing_type, "DELIVERED",
                    )
            except BostaInventoryStockBlocked as exc:
                return self._set_effect(effect, "blocked_stock", str(exc))
            return self._set_effect(
                effect,
                "delivered_finalized",
                final_picking_id=final.id,
                delivered_finalized_at=fields.Datetime.now(),
            )

        if stage in _REVIEW_STAGES:
            return self._set_effect(effect, "exception", "Outbound remains applied; terminal state requires review.")
        # Returning/returned-to-origin stays in Bosta Transit. Phase 8 restores
        # only after safe physical-return evidence.
        return effect
