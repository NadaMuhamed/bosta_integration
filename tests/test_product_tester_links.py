from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from ..services.bosta_product_mapping_service import BostaProductMappingService


class TestBostaTesterLinks(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Bosta Phase7 Tester Company"})
        cls.other_company = cls.env["res.company"].create({"name": "Bosta Phase7 Tester Other"})
        cls.ProductTemplate = cls.env["product.template"].sudo().with_company(cls.company)

    def _product(self, name, code, company=None):
        company = company if company is not None else self.company
        tmpl = self.env["product.template"].sudo().with_company(self.company).create({
            "name": name,
            "default_code": code,
            "type": "consu",
            "is_storable": True,
            "company_id": company.id if company else False,
        })
        return tmpl.product_variant_id

    def test_explicit_main_tester_relation(self):
        main = self._product("P7 REL MAIN", "P7REL")
        tester = self._product("P7 REL MAIN 3 ML", "P7REL")
        tester.write({"bosta_product_role": "tester"})
        main.write({"bosta_product_role": "main", "bosta_tester_required": True, "bosta_tester_product_id": tester.id})
        self.assertEqual(main.bosta_tester_product_id, tester)

    def test_main_cannot_link_to_itself(self):
        main = self._product("P7 SELF", "P7SELF")
        with self.assertRaises(ValidationError):
            main.write({"bosta_product_role": "main", "bosta_tester_product_id": main.id})

    def test_linked_tester_must_have_tester_role(self):
        main = self._product("P7 ROLE", "P7ROLE")
        other = self._product("P7 ROLE OTHER", "P7ROLE")
        with self.assertRaises(ValidationError):
            main.write({"bosta_product_role": "main", "bosta_tester_product_id": other.id})

    def test_cross_company_relation_rejected(self):
        main = self._product("P7 CROSS", "P7CROSS", self.company)
        tester = self._product("P7 CROSS 3 ML", "P7CROSS", self.other_company)
        tester.with_company(self.other_company).write({"bosta_product_role": "tester"})
        with self.assertRaises(ValidationError):
            main.write({"bosta_product_role": "main", "bosta_tester_product_id": tester.id})

    def test_shared_tester_is_safe(self):
        main = self._product("P7 SHARED", "P7SHARED", self.company)
        tester = self._product("P7 SHARED 3 ML", "P7SHARED", False)
        tester.write({"bosta_product_role": "tester"})
        main.write({"bosta_product_role": "main", "bosta_tester_product_id": tester.id})
        self.assertEqual(main.bosta_tester_product_id, tester)

    def test_26_bootstrap_exact_pair_links(self):
        main = self._product("P7 BOOT", "P7BOOT")
        tester = self._product("P7 BOOT 3 ML", "P7BOOT")
        counts = BostaProductMappingService(self.env, self.company).bootstrap_tester_links()
        main.invalidate_recordset(); tester.invalidate_recordset()
        self.assertEqual(main.bosta_product_role, "main")
        self.assertEqual(tester.bosta_product_role, "tester")
        self.assertEqual(main.bosta_tester_product_id, tester)
        self.assertTrue(main.bosta_tester_required)
        self.assertGreaterEqual(counts["linked"], 1)

    def test_27_bootstrap_is_idempotent(self):
        main = self._product("P7 IDEMP", "P7IDEMP")
        tester = self._product("P7 IDEMP 3 ML", "P7IDEMP")
        service = BostaProductMappingService(self.env, self.company)
        service.bootstrap_tester_links()
        second = service.bootstrap_tester_links()
        self.assertEqual(main.bosta_tester_product_id, tester)
        self.assertGreaterEqual(second["already_linked"], 1)

    def test_29_two_testers_are_conflict_no_guess(self):
        main = self._product("P7 MULTI T", "P7MT")
        t1 = self._product("P7 MULTI T 3 ML", "P7MT")
        t2 = self._product("P7 MULTI T EXTRA 3 ML", "P7MT")
        counts = BostaProductMappingService(self.env, self.company).bootstrap_tester_links()
        self.assertFalse(main.bosta_tester_product_id)
        self.assertGreaterEqual(counts["conflicts"], 1)
        self.assertTrue(t1 and t2)

    def test_30_two_mains_are_conflict_no_guess(self):
        m1 = self._product("P7 MULTI M A", "P7MM")
        m2 = self._product("P7 MULTI M B", "P7MM")
        self._product("P7 MULTI M 3 ML", "P7MM")
        counts = BostaProductMappingService(self.env, self.company).bootstrap_tester_links()
        self.assertFalse(m1.bosta_tester_product_id)
        self.assertFalse(m2.bosta_tester_product_id)
        self.assertGreaterEqual(counts["conflicts"], 1)

    def test_31_missing_tester_is_skipped(self):
        main = self._product("P7 NO TESTER", "P7NOT")
        counts = BostaProductMappingService(self.env, self.company).bootstrap_tester_links()
        self.assertFalse(main.bosta_tester_product_id)
        self.assertGreaterEqual(counts["skipped"], 1)

    def test_bootstrap_never_renames_or_changes_code(self):
        main = self._product("P7 KEEP NAME", "P7KEEP")
        tester = self._product("P7 KEEP NAME 3 ML", "P7KEEP")
        before = (main.name, main.default_code, tester.name, tester.default_code)
        BostaProductMappingService(self.env, self.company).bootstrap_tester_links()
        self.assertEqual((main.name, main.default_code, tester.name, tester.default_code), before)
