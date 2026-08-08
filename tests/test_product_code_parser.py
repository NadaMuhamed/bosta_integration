from copy import deepcopy
from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase

from ..services.bosta_product_code_parser import (
    extract_business_code,
    parse_package_description,
    parse_package_line,
)


class TestBostaProductCodeParser(TransactionCase):
    def test_36_parse_business_code_521(self):
        self.assertEqual(extract_business_code("088.01-521.050"), "521")

    def test_37_preserve_leading_zero_code(self):
        self.assertEqual(extract_business_code("088.01-002.050"), "002")

    def test_38_parse_explicit_quantity_one(self):
        row = parse_package_line("40 Night Unisex Extrait De Perfume x 1 (088.01-521.050)")
        self.assertEqual(row["quantity"], 1)
        self.assertEqual(row["source_product_code"], "521")

    def test_39_parse_explicit_quantity_two(self):
        row = parse_package_line("A-Vision Men's Extrait De Perfume x 2 (088.01-002.050)")
        self.assertEqual(row["quantity"], 2)
        self.assertEqual(row["source_product_code"], "002")

    def test_40_malformed_code_returns_no_candidate(self):
        self.assertIsNone(parse_package_line("Thing x 1 (not-a-bosta-code)"))

    def test_41_no_parentheses_never_guesses(self):
        self.assertIsNone(parse_package_line("Thing x 1 code 521"))

    def test_42_multiline_deterministic_parsing(self):
        result = parse_package_description(
            "A x 1 (088.01-002.050)\nB x 2 (088.01-521.050)"
        )
        self.assertFalse(result["ambiguous"])
        self.assertEqual([r["source_product_code"] for r in result["candidates"]], ["002", "521"])
        self.assertEqual([r["quantity"] for r in result["candidates"]], [1, 2])

    def test_43_one_bad_multiline_row_blocks_entire_parse(self):
        result = parse_package_description(
            "A x 1 (088.01-002.050)\nUnstructured second product"
        )
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["candidates"], [])

    def test_44_parser_source_contains_no_orm(self):
        path = Path(get_module_path("bosta_integration")) / "services" / "bosta_product_code_parser.py"
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ('env[', '.create(', '.write(', '.unlink('):
            self.assertNotIn(forbidden, text)

    def test_45_parser_source_contains_no_http(self):
        path = Path(get_module_path("bosta_integration")) / "services" / "bosta_product_code_parser.py"
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ('requests.', 'httpx', 'urllib', 'authorization'):
            self.assertNotIn(forbidden, text)

    def test_46_parser_does_not_mutate_input(self):
        source = "A x 1 (088.01-002.050)\nB x 2 (088.01-521.050)"
        before = deepcopy(source)
        parse_package_description(source)
        self.assertEqual(source, before)

    def test_47_parser_does_not_create_delivery_items(self):
        before = self.env["bosta.delivery.item"].search_count([])
        parse_package_description("A x 1 (088.01-002.050)")
        self.assertEqual(self.env["bosta.delivery.item"].search_count([]), before)

    def test_48_observed_bracket_comma_format_is_deterministic(self):
        result = parse_package_description(
            "A-Vision Men's Extrait De Perfume [088.01-002.050] (1), "
            "Daring Fire Men's Extrait De Perfume [088.01-009.050] (2)"
        )
        self.assertFalse(result["ambiguous"])
        self.assertEqual([r["source_product_code"] for r in result["candidates"]], ["002", "009"])
        self.assertEqual([r["quantity"] for r in result["candidates"]], [1, 2])

    def test_49_malformed_observed_format_still_blocks_entire_parse(self):
        result = parse_package_description(
            "( 1 Sweet Whisper x 1 (088.01-1154.050) Famous Women Extrait De Perfume"
        )
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["candidates"], [])
