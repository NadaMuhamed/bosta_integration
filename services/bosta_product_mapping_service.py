"""Conservative Phase 7 Bosta-to-Odoo product mapping service."""

from odoo import fields

from .bosta_product_code_parser import parse_package_description
from .bosta_product_helpers import canonical_business_code, looks_like_tester_name


class BostaProductMappingService:
    def __init__(self, env, company):
        self.env = env
        self.company = company
        allowed = list(env.context.get("allowed_company_ids") or env.user.company_ids.ids)
        if company.id not in allowed:
            allowed.append(company.id)
        self.ctx = dict(env.context, allowed_company_ids=allowed)
        self.Mapping = env["bosta.product.mapping"].with_context(self.ctx).with_company(company)
        self.Product = env["product.product"].with_context(self.ctx).with_company(company)

    def _product_company_domain(self):
        return ["|", ("company_id", "=", False), ("company_id", "=", self.company.id)]

    def _mapping_identity_domain(self, candidate, creation_source):
        external_id = candidate.get("external_product_id")
        code = canonical_business_code(candidate.get("source_product_code"))
        base = [
            ("company_id", "=", self.company.id),
            ("creation_source", "=", creation_source or "BOSTA"),
        ]
        if external_id not in (None, False, ""):
            return base + [("external_product_id", "=", str(external_id).strip())]
        if code:
            return base + [("external_product_id", "=", False), ("source_product_code", "=", code)]
        return None

    @staticmethod
    def _valid_main(product, company):
        return bool(
            product
            and product.bosta_product_role == "main"
            and (not product.company_id or product.company_id == company)
        )

    def _identity_mapping(self, creation_source, identity_key):
        if not identity_key:
            return self.Mapping.browse()
        return self.Mapping.sudo().search([
            ("company_id", "=", self.company.id),
            ("creation_source", "=", creation_source or "BOSTA"),
            ("identity_key", "=", identity_key),
        ], limit=1)

    def _touch_existing_mapping(self, mapping, candidate):
        """Record a sighting without changing the mapping's primary identity."""
        vals = {
            "last_seen_at": fields.Datetime.now(),
            "seen_count": mapping.seen_count + 1,
            "source_title": candidate.get("source_title") or mapping.source_title,
        }
        # Secondary observations are safe only when they cannot promote a code
        # identity into a new external identity. In particular, a code mapping
        # stays a code mapping when a later observation gains external_product_id.
        if mapping.external_product_id:
            code = canonical_business_code(candidate.get("source_product_code"))
            if code:
                vals["source_product_code"] = code
            product_info_id = candidate.get("bosta_product_info_id")
            if product_info_id not in (None, False, ""):
                vals["bosta_product_info_id"] = str(product_info_id).strip()
        elif mapping.source_product_code:
            product_info_id = candidate.get("bosta_product_info_id")
            if product_info_id not in (None, False, ""):
                vals["bosta_product_info_id"] = str(product_info_id).strip()
        mapping.write(vals)
        return mapping

    def _valid_authoritative_mapping(self, mapping):
        return bool(
            mapping
            and mapping.mapping_status == "mapped"
            and self._valid_main(mapping.odoo_product_id, self.company)
        )

    def _observe_mapping(self, candidate, creation_source, *, status, product=False, method=False):
        domain = self._mapping_identity_domain(candidate, creation_source)
        if not domain:
            return self.Mapping.browse()
        Mapping = self.Mapping.sudo()
        mapping = Mapping.search(domain, limit=1)
        now = fields.Datetime.now()
        vals = {
            "source_title": candidate.get("source_title") or False,
            "last_seen_at": now,
            "seen_count": (mapping.seen_count if mapping else 0) + 1,
        }
        if candidate.get("source_product_code"):
            vals["source_product_code"] = canonical_business_code(candidate["source_product_code"])
        if candidate.get("external_product_id") not in (None, False, ""):
            vals["external_product_id"] = str(candidate["external_product_id"]).strip()
        if candidate.get("bosta_product_info_id") not in (None, False, ""):
            vals["bosta_product_info_id"] = str(candidate["bosta_product_info_id"]).strip()
        if mapping and mapping.mapping_status == "mapped" and self._valid_main(mapping.odoo_product_id, self.company):
            # Never let a later heuristic overwrite an explicit valid mapping.
            mapping.write(vals)
            return mapping
        vals.update({"mapping_status": status, "odoo_product_id": product.id if product else False})
        if method:
            vals["mapping_method"] = method
        if mapping:
            mapping.write(vals)
            return mapping
        vals.update({
            "company_id": self.company.id,
            "creation_source": creation_source or "BOSTA",
        })
        return Mapping.create(vals)

    def resolve_candidate(self, candidate, creation_source="BOSTA"):
        """Resolve one observed Bosta product. No fuzzy/title auto-mapping.

        Authoritative precedence is intentionally identity-aware:
        1) existing mapped external ID;
        2) existing mapped deterministic code for the same company/source;
        3) fresh exact business-code resolution;
        4) unmatched/conflict.

        A later stronger observation does not erase an earlier authoritative code
        mapping or silently create an alias.
        """
        source = creation_source or "BOSTA"
        external_id = candidate.get("external_product_id")
        if external_id not in (None, False, ""):
            external_key = "external:%s" % str(external_id).strip()
            existing_external = self._identity_mapping(source, external_key)
            if self._valid_authoritative_mapping(existing_external):
                self._touch_existing_mapping(existing_external, candidate)
                return {
                    "status": "mapped",
                    "mapping": existing_external,
                    "product": existing_external.odoo_product_id,
                }

        code = canonical_business_code(candidate.get("source_product_code"))
        if code:
            existing_code = self._identity_mapping(source, "code:%s" % code)
            if self._valid_authoritative_mapping(existing_code):
                self._touch_existing_mapping(existing_code, candidate)
                return {
                    "status": "mapped",
                    "mapping": existing_code,
                    "product": existing_code.odoo_product_id,
                }

        if code:
            # IMPORTANT: only the explicit business code is compared to default_code.
            # Bosta productId is never treated as an Internal Reference.
            candidates = self.Product.sudo().search([
                ("default_code", "=", code),
                ("bosta_product_role", "=", "main"),
                *self._product_company_domain(),
            ])
            if len(candidates) == 1:
                product = candidates[0]
                mapping = self._observe_mapping(
                    candidate, source, status="mapped", product=product,
                    method="exact_business_code",
                )
                return {"status": "mapped", "mapping": mapping, "product": product}
            if len(candidates) > 1:
                mapping = self._observe_mapping(candidate, source, status="conflict")
                return {"status": "conflict", "mapping": mapping, "product": False}

        mapping = self._observe_mapping(candidate, source, status="unmatched")
        return {"status": "unmatched", "mapping": mapping, "product": False}

    def delivery_candidates(self, delivery):
        """Return trustworthy inventory candidates without fabricating item rows."""
        delivery.ensure_one()
        creation_source = delivery.creation_source or "BOSTA"
        package = parse_package_description(delivery.package_description or "")

        if delivery.item_ids:
            items = delivery.item_ids.sorted(lambda row: (row.sequence, row.id))
            parsed = package["candidates"] if not package["ambiguous"] else []
            can_correlate = len(parsed) == len(items)
            candidates = []
            for index, item in enumerate(items):
                candidate = {
                    "external_product_id": item.external_product_id or False,
                    "bosta_product_info_id": item.bosta_product_info_id or False,
                    "source_title": item.title or False,
                    "quantity": item.quantity,
                }
                # Package description is supplemental evidence only. Correlate
                # it with authoritative productInfo rows only when the whole
                # ordered set agrees on title and quantity.
                if can_correlate:
                    parsed_row = parsed[index]
                    title_ok = (item.title or "").strip().casefold() == parsed_row["title"].strip().casefold()
                    qty_ok = item.quantity == parsed_row["quantity"]
                    if title_ok and qty_ok:
                        candidate["source_product_code"] = parsed_row["source_product_code"]
                    else:
                        can_correlate = False
                candidates.append(candidate)
            if not can_correlate:
                for candidate in candidates:
                    candidate.pop("source_product_code", None)
            return creation_source, candidates

        # Search payload often has package description but no productInfo. It
        # may become a Phase 7 candidate only if the full description parses.
        if package["ambiguous"]:
            return creation_source, []
        return creation_source, [
            {
                "source_product_code": row["source_product_code"],
                "source_title": row["title"],
                "quantity": row["quantity"],
            }
            for row in package["candidates"]
        ]

    def resolve_delivery(self, delivery):
        creation_source, candidates = self.delivery_candidates(delivery)
        if not candidates:
            return {"resolved": False, "reason": "No deterministic product candidates.", "lines": []}
        resolved_lines = []
        blocked = []
        for candidate in candidates:
            qty = candidate.get("quantity")
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty <= 0:
                blocked.append("Invalid or missing quantity.")
                continue
            result = self.resolve_candidate(candidate, creation_source=creation_source)
            if result["status"] != "mapped" or not result["product"]:
                blocked.append("Product mapping is unresolved or conflicting.")
                continue
            product = result["product"]
            tester = product.bosta_tester_product_id
            if product.bosta_tester_required and not tester:
                blocked.append("Required tester link is missing.")
                resolved_lines.append({"candidate": candidate, "mapping": result["mapping"], "product": product, "tester": False, "quantity": qty, "tester_missing": True})
                continue
            resolved_lines.append({
                "candidate": candidate,
                "mapping": result["mapping"],
                "product": product,
                "tester": tester if product.bosta_tester_required else False,
                "quantity": qty,
                "tester_missing": False,
            })
        return {
            "resolved": not blocked and len(resolved_lines) == len(candidates),
            "reason": blocked[0] if blocked else False,
            "lines": resolved_lines,
        }

    def bootstrap_tester_links(self):
        """Conservatively persist MAIN/tester relations from the existing catalog."""
        Product = self.Product.sudo().with_context(active_test=False)
        products = Product.search([
            ("default_code", "!=", False),
            *self._product_company_domain(),
        ])
        groups = {}
        empty_products = Product.browse()
        for product in products:
            code = canonical_business_code(product.default_code)
            if code:
                groups[code] = groups.get(code, empty_products) | product

        counts = {"linked": 0, "already_linked": 0, "conflicts": 0, "skipped": 0}
        for _code, rows in groups.items():
            testers = rows.filtered(lambda p: looks_like_tester_name(p.display_name or p.name))
            mains = rows - testers
            if not testers:
                counts["skipped"] += 1
                continue
            if len(testers) != 1 or len(mains) != 1:
                counts["conflicts"] += 1
                continue
            main, tester = mains[0], testers[0]
            if main.company_id and tester.company_id and main.company_id != tester.company_id:
                counts["conflicts"] += 1
                continue
            if (
                main.bosta_product_role == "main"
                and main.bosta_tester_product_id == tester
                and main.bosta_tester_required
                and tester.bosta_product_role == "tester"
            ):
                counts["already_linked"] += 1
                continue
            # Non-destructive: never overwrite a conflicting explicit link/role.
            if main.bosta_tester_product_id and main.bosta_tester_product_id != tester:
                counts["conflicts"] += 1
                continue
            if main.bosta_product_role == "tester" or tester.bosta_product_role == "main":
                counts["conflicts"] += 1
                continue
            tester.write({"bosta_product_role": "tester", "bosta_tester_required": False, "bosta_tester_product_id": False})
            main.write({
                "bosta_product_role": "main",
                "bosta_tester_required": True,
                "bosta_tester_product_id": tester.id,
            })
            counts["linked"] += 1
        return counts
