from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_product_mapping_service import BostaProductMappingService


class TestBostaProductMapping(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Bosta P7 Mapping A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta P7 Mapping B"})
        cls.manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager = cls._user("p7-map-manager", cls.company, [cls.company, cls.company_b], cls.manager_group)
        cls.ordinary = cls._user("p7-map-user", cls.company, [cls.company], cls.user_group)

    @classmethod
    def _user(cls, login, company, companies, group):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.invalid",
            "company_id": company.id,
            "company_ids": [(6, 0, [c.id for c in companies])],
            "groups_id": [(6, 0, [group.id])],
        })

    def _product(self, name, code, role="main", company=None):
        company = company or self.company
        tmpl = self.env["product.template"].sudo().with_company(company).create({
            "name": name, "default_code": code, "type": "consu", "is_storable": True,
            "company_id": company.id,
        })
        product = tmpl.product_variant_id
        product.write({"bosta_product_role": role})
        return product

    def _service(self, company=None):
        return BostaProductMappingService(self.env, company or self.company)

    def test_01_product_id_is_not_default_code(self):
        product = self._product("P7 External Is Not Code", "1214838")
        result = self._service().resolve_candidate({
            "external_product_id": "1214838", "source_title": "Unrelated Bosta Identity"
        }, "SHOPIFY")
        self.assertEqual(result["status"], "unmatched")
        self.assertNotEqual(result["product"], product)

    def test_02_existing_persistent_mapping_wins(self):
        chosen = self._product("P7 Chosen", "P7CHOSEN")
        other = self._product("P7 Other", "P7OTHER")
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "external_product_id": "ext-existing", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": chosen.id,
        })
        result = self._service().resolve_candidate({
            "external_product_id": "ext-existing", "source_product_code": other.default_code,
            "source_title": "Changed title",
        }, "SHOPIFY")
        self.assertEqual(result["product"], chosen)
        self.assertEqual(result["mapping"], mapping)

    def test_03_exact_code_maps_only_main(self):
        main = self._product("P7 Main", "P7003", "main")
        self._product("P7 Main 3 ML", "P7003", "tester")
        result = self._service().resolve_candidate({"source_product_code": "P7003", "source_title": "Any"})
        self.assertEqual(result["status"], "mapped")
        self.assertEqual(result["product"], main)

    def test_04_same_code_main_tester_never_random(self):
        main = self._product("P7 Same Code", "P7004", "main")
        tester = self._product("P7 Same Code 3 ML", "P7004", "tester")
        result = self._service().resolve_candidate({"source_product_code": "P7004"})
        self.assertEqual(result["product"], main)
        self.assertNotEqual(result["product"], tester)

    def test_11_exact_one_main_succeeds(self):
        main = self._product("P7 Exact One", "P7011")
        result = self._service().resolve_candidate({"source_product_code": "P7011"})
        self.assertEqual(result["product"], main)

    def test_12_multiple_main_candidates_conflict(self):
        self._product("P7 Main A", "P7012")
        self._product("P7 Main B", "P7012")
        result = self._service().resolve_candidate({"source_product_code": "P7012"})
        self.assertEqual(result["status"], "conflict")
        self.assertFalse(result["product"])
        self.assertEqual(result["mapping"].mapping_status, "conflict")

    def test_13_missing_code_is_not_guessed(self):
        self._product("P7 Missing Code Similar Title", "P7013")
        result = self._service().resolve_candidate({"source_title": "P7 Missing Code Similar Title"})
        self.assertEqual(result["status"], "unmatched")

    def test_14_title_only_is_not_authoritative(self):
        product = self._product("DIELMA", "P7014")
        result = self._service().resolve_candidate({"source_title": "Dilema"})
        self.assertEqual(result["status"], "unmatched")
        self.assertNotEqual(result["product"], product)

    def test_16_unmatched_mapping_persists(self):
        result = self._service().resolve_candidate({"external_product_id": "unmatched-16", "source_title": "No match"}, "SHOPIFY")
        self.assertTrue(result["mapping"])
        self.assertEqual(result["mapping"].mapping_status, "unmatched")

    def test_17_conflict_mapping_persists(self):
        self._product("P7 Conflict A", "P7017")
        self._product("P7 Conflict B", "P7017")
        result = self._service().resolve_candidate({"source_product_code": "P7017"})
        self.assertEqual(result["mapping"].mapping_status, "conflict")

    def test_18_manager_manual_mapping_succeeds(self):
        product = self._product("P7 Manual", "P7018")
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "external_product_id": "manual-18", "mapping_status": "unmatched",
        })
        manager_mapping = mapping.with_user(self.manager).with_context(allowed_company_ids=[self.company.id])
        manager_mapping.write({"odoo_product_id": product.id})
        self.assertEqual(mapping.mapping_status, "mapped")
        self.assertEqual(mapping.mapping_method, "manual")

    def test_19_ordinary_user_cannot_modify_mapping(self):
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "external_product_id": "readonly-19", "mapping_status": "unmatched",
        })
        with self.assertRaises(AccessError):
            mapping.with_user(self.ordinary).with_context(allowed_company_ids=[self.company.id]).write({"source_title": "No"})

    def test_20_mapping_company_isolated(self):
        self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company_b.id, "creation_source": "SHOPIFY",
            "external_product_id": "isolated-20", "mapping_status": "unmatched",
        })
        visible = self.env["bosta.product.mapping"].with_user(self.ordinary).with_context(allowed_company_ids=[self.company.id]).search([
            ("external_product_id", "=", "isolated-20")
        ])
        self.assertFalse(visible)

    def test_21_no_odoo_product_is_auto_created(self):
        before = self.env["product.product"].sudo().search_count([])
        self._service().resolve_candidate({"external_product_id": "no-auto-product", "source_title": "No Product"})
        self.assertEqual(self.env["product.product"].sudo().search_count([]), before)

    def test_22_mapping_repeat_is_idempotent(self):
        service = self._service()
        first = service.resolve_candidate({"external_product_id": "repeat-22", "source_title": "A"}, "SHOPIFY")
        second = service.resolve_candidate({"external_product_id": "repeat-22", "source_title": "A"}, "SHOPIFY")
        self.assertEqual(first["mapping"], second["mapping"])
        self.assertEqual(self.env["bosta.product.mapping"].sudo().search_count([
            ("company_id", "=", self.company.id), ("creation_source", "=", "SHOPIFY"),
            ("external_product_id", "=", "repeat-22")
        ]), 1)

    def test_23_source_title_is_not_unique_identity(self):
        service = self._service()
        a = service.resolve_candidate({"external_product_id": "title-a", "source_title": "Same Title"}, "SHOPIFY")["mapping"]
        b = service.resolve_candidate({"external_product_id": "title-b", "source_title": "Same Title"}, "SHOPIFY")["mapping"]
        self.assertNotEqual(a, b)

    def test_24_external_id_namespace_is_source_safe(self):
        service = self._service()
        a = service.resolve_candidate({"external_product_id": "same-ext"}, "SHOPIFY")["mapping"]
        b = service.resolve_candidate({"external_product_id": "same-ext"}, "API")["mapping"]
        self.assertNotEqual(a, b)

    def test_25_leading_zero_code_is_preserved(self):
        main = self._product("P7 Leading Zero", "002")
        result = self._service().resolve_candidate({"source_product_code": "002", "source_title": "A-Vision"})
        self.assertEqual(result["product"], main)
        self.assertEqual(result["mapping"].source_product_code, "002")

    def test_26_existing_code_mapping_survives_later_external_identity(self):
        literal = self._product("P7 Literal 521", "521")
        chosen = self._product("P7 Manual Choice 521", "OTHER-521")
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "source_product_code": "521", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": chosen.id,
        })
        result = self._service().resolve_candidate({
            "external_product_id": "123456",
            "source_product_code": "521",
            "source_title": "Later stronger observation",
        }, "SHOPIFY")
        self.assertEqual(result["product"], chosen)
        self.assertEqual(result["mapping"], mapping)
        self.assertNotEqual(result["product"], literal)
        self.assertFalse(mapping.external_product_id)
        self.assertEqual(mapping.identity_key, "code:521")

    def test_27_manual_code_mapping_beats_literal_default_code_lookup(self):
        literal = self._product("P7 Literal 527", "527")
        manual = self._product("P7 Manual 527", "MANUAL-527")
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "source_product_code": "527", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": manual.id,
        })
        result = self._service().resolve_candidate({
            "external_product_id": "ext-527", "source_product_code": "527",
        }, "SHOPIFY")
        self.assertEqual(result["mapping"], mapping)
        self.assertEqual(result["product"], manual)
        self.assertNotEqual(result["product"], literal)

    def test_28_existing_external_mapping_has_highest_precedence(self):
        external_product = self._product("P7 External Winner", "EXT-WIN")
        code_product = self._product("P7 Code Winner", "CODE-WIN")
        external_mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "external_product_id": "ext-priority-28", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": external_product.id,
        })
        self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "source_product_code": "CODE-WIN", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": code_product.id,
        })
        result = self._service().resolve_candidate({
            "external_product_id": "ext-priority-28",
            "source_product_code": "CODE-WIN",
        }, "SHOPIFY")
        self.assertEqual(result["mapping"], external_mapping)
        self.assertEqual(result["product"], external_product)

    def test_29_identity_evolution_does_not_create_duplicate_authority(self):
        chosen = self._product("P7 Existing Authority", "AUTH-OTHER")
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "SHOPIFY",
            "source_product_code": "529", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": chosen.id,
        })
        before = self.env["bosta.product.mapping"].sudo().search_count([
            ("company_id", "=", self.company.id),
            ("creation_source", "=", "SHOPIFY"),
        ])
        result = self._service().resolve_candidate({
            "external_product_id": "ext-529", "source_product_code": "529",
        }, "SHOPIFY")
        after = self.env["bosta.product.mapping"].sudo().search_count([
            ("company_id", "=", self.company.id),
            ("creation_source", "=", "SHOPIFY"),
        ])
        self.assertEqual(result["mapping"], mapping)
        self.assertEqual(after, before)
        self.assertFalse(self.env["bosta.product.mapping"].sudo().search([
            ("company_id", "=", self.company.id),
            ("creation_source", "=", "SHOPIFY"),
            ("external_product_id", "=", "ext-529"),
        ]))

    def test_30_code_authority_is_company_and_source_isolated(self):
        literal = self._product("P7 Literal Source Safe", "530")
        wrong_source = self._product("P7 Wrong Source Manual", "OTHER-530")
        self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id, "creation_source": "API",
            "source_product_code": "530", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": wrong_source.id,
        })
        result = self._service().resolve_candidate({
            "external_product_id": "ext-530", "source_product_code": "530",
        }, "SHOPIFY")
        self.assertEqual(result["product"], literal)
        self.assertNotEqual(result["product"], wrong_source)
        self.assertEqual(result["mapping"].creation_source, "SHOPIFY")

    def test_31_code_authority_is_company_isolated_during_resolution(self):
        literal_a = self._product("P7 Company A Literal", "531", company=self.company)
        chosen_b = self._product("P7 Company B Manual", "OTHER-531", company=self.company_b)
        self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company_b.id, "creation_source": "SHOPIFY",
            "source_product_code": "531", "mapping_status": "mapped",
            "mapping_method": "manual", "odoo_product_id": chosen_b.id,
        })
        result = self._service(self.company).resolve_candidate({
            "external_product_id": "ext-531", "source_product_code": "531",
        }, "SHOPIFY")
        self.assertEqual(result["product"], literal_a)
        self.assertNotEqual(result["product"], chosen_b)
        self.assertEqual(result["mapping"].company_id, self.company)


    def test_mapping_requires_stable_identity(self):
        with self.assertRaises(ValidationError):
            self.env["bosta.product.mapping"].sudo().create({
                "company_id": self.company.id,
                "creation_source": "SHOPIFY",
                "source_title": "Title alone is not identity",
                "mapping_status": "unmatched",
            })

    def test_mapping_external_product_id_only_is_allowed(self):
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id,
            "creation_source": "SHOPIFY",
            "external_product_id": "stable-external-only",
            "source_title": "Observed title",
            "mapping_status": "unmatched",
        })
        self.assertEqual(mapping.identity_key, "external:stable-external-only")

    def test_mapping_source_product_code_only_is_allowed(self):
        mapping = self.env["bosta.product.mapping"].sudo().create({
            "company_id": self.company.id,
            "creation_source": "SHOPIFY",
            "source_product_code": "002",
            "source_title": "Observed title",
            "mapping_status": "unmatched",
        })
        self.assertEqual(mapping.identity_key, "code:002")

    def test_mapping_product_info_and_title_without_stable_identity_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["bosta.product.mapping"].sudo().create({
                "company_id": self.company.id,
                "creation_source": "SHOPIFY",
                "bosta_product_info_id": "secondary-only",
                "source_title": "Still not identity",
                "mapping_status": "unmatched",
            })
