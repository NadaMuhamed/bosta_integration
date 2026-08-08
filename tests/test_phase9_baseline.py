import ast
from pathlib import Path
from unittest import TestCase
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class TestPhase9Baseline(TestCase):
    def _runtime(self):
        return "\n".join(
            p.read_text(encoding="utf-8").lower()
            for folder in ("models", "services")
            for p in sorted((ROOT / folder).glob("*.py"))
        )

    def test_01_manifest_version_dependencies_installable(self):
        manifest = ast.literal_eval((ROOT / "__manifest__.py").read_text())
        self.assertEqual(manifest["version"], "18.0.11.0.0")
        self.assertEqual(manifest["depends"], ["base", "stock"])
        self.assertTrue(manifest["installable"])
        for forbidden in ("sale", "account", "purchase", "website", "contacts", "crm"):
            self.assertNotIn(forbidden, manifest["depends"])

    def test_02_financial_models_and_services_loaded(self):
        self.assertIn("bosta_financial", (ROOT / "models" / "__init__.py").read_text())
        services = (ROOT / "services" / "__init__.py").read_text()
        self.assertIn("bosta_financial_service", services)
        self.assertIn("bosta_financial_enrichment_service", services)

    def test_03_financial_snapshot_db_uniqueness(self):
        source = (ROOT / "models" / "bosta_financial.py").read_text()
        self.assertIn("unique(company_id, delivery_id)", source)
        self.assertIn("unique(financial_id, original_inventory_effect_line_id, role)", source)

    def test_04_revenue_is_not_automatically_cod(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertNotIn("recognized_revenue_amount = delivery.cod_amount", source)
        self.assertNotIn('"recognized_revenue_amount": delivery.cod_amount', source)
        model = (ROOT / "models" / "bosta_financial.py").read_text()
        self.assertIn("action_confirm_cod_as_revenue", model)
        self.assertIn('"cod_confirmed"', model)

    def test_05_missing_and_zero_fee_presence_is_explicit(self):
        delivery = (ROOT / "models" / "bosta_delivery.py").read_text()
        persistence = (ROOT / "services" / "bosta_persistence_service.py").read_text()
        self.assertIn("shipment_fees_present", delivery)
        self.assertIn('"shipment_fees": "shipment_fees_present"', persistence)
        self.assertIn('if source_field in raw_values', persistence)

    def test_06_cost_uses_phase7_inventory_snapshot_not_mapping_resolution(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn("bosta.inventory.effect", source)
        self.assertIn("original_inventory_effect_line_id", source)
        self.assertNotIn("BostaProductMappingService", source)
        self.assertNotIn("bosta.product.mapping", source)

    def test_07_cost_source_named_truthfully(self):
        source = (ROOT / "models" / "bosta_financial.py").read_text()
        self.assertIn('("product_standard_price", "Product Standard Price")', source)
        service = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn("value > 0", service)
        self.assertNotIn("purchase_invoice", source.lower())

    def test_08_return_credit_requires_phase8_applied_restoration(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn('env["bosta.return.restoration.effect.line"]', source)
        self.assertIn('(\"effect_id.status\", \"=\", \"applied\")', source)
        self.assertIn("original_inventory_effect_line_id", source)

    def test_09_finance_never_matches_returns_by_business_reference(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text().lower()
        self.assertNotIn("business_reference", source)
        self.assertNotIn("receiver_phone", source)
        self.assertNotIn("dropoff", source)

    def test_10_no_fake_sale_account_purchase_or_customer_documents(self):
        runtime = self._runtime()
        for forbidden in (
            'env["sale.order"]', "env['sale.order']",
            'env["sale.order.line"]', "env['sale.order.line']",
            'env["account.move"]', "env['account.move']",
            'env["purchase.order"]', "env['purchase.order']",
            'env["res.partner"]', "env['res.partner']",
        ):
            self.assertNotIn(forbidden, runtime)

    def test_11_no_direct_stock_quant_in_phase9_runtime(self):
        runtime = "\n".join([
            (ROOT / "models" / "bosta_financial.py").read_text().lower(),
            (ROOT / "services" / "bosta_financial_service.py").read_text().lower(),
            (ROOT / "services" / "bosta_financial_enrichment_service.py").read_text().lower(),
        ])
        self.assertNotIn('env["stock.quant"]', runtime)
        self.assertNotIn("_update_available_quantity", runtime)

    def test_12_financial_statuses_include_incomplete_and_review(self):
        source = (ROOT / "models" / "bosta_financial.py").read_text()
        for value in ("not_ready", "incomplete", "ready", "calculated", "finalized", "review_required"):
            self.assertIn(f'(\"{value}\"', source)

    def test_13_formula_excludes_unknown_inputs(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn('financial.revenue_source == "not_available"', source)
        self.assertIn('financial.logistics_cost_status != "authoritative"', source)
        self.assertIn('financial.return_fee_source == "not_available"', source)
        self.assertIn("financial.recognized_revenue_amount", source)
        self.assertIn("- financial.net_cogs_amount", source)
        self.assertIn("- financial.logistics_cost_amount", source)
        self.assertIn("- financial.return_fee_amount", source)
        self.assertIn("+ financial.compensation_amount", source)

    def test_14_shipment_fees_is_not_added_to_alias_components(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        shipment_branch = source.split("if delivery.shipment_fees_present:", 1)[1].split("component_present =", 1)[0]
        self.assertNotIn("delivery.shipping_fee +", shipment_branch)
        self.assertNotIn("opening_package_fee +", shipment_branch)

    def test_15_customer_return_fee_is_not_invented(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn('"return_fee_source": "not_available"', source)
        self.assertIn('"return_fee_source": "not_applicable"', source)

    def test_16_cron_is_one_shared_opt_in_runner(self):
        cron = (ROOT / "data" / "bosta_cron.xml").read_text().lower()
        config = (ROOT / "models" / "bosta_config.py").read_text()
        self.assertIn('model="ir.cron"', cron)
        self.assertIn("model._cron_sync_due_configs()", cron)
        self.assertIn('auto_sync_enabled = fields.Boolean(string="Auto Sync Enabled", default=False', config)
        self.assertIn('(\"auto_sync_enabled\", \"=\", True)', config)
        self.assertIn("action_sync_bosta_deliveries()", config)

    def test_17_cron_minimum_interval_is_five_minutes(self):
        config = (ROOT / "models" / "bosta_config.py").read_text()
        cron = (ROOT / "data" / "bosta_cron.xml").read_text()
        self.assertIn("auto_sync_interval_minutes < 5", config)
        self.assertIn("<field name=\"interval_number\">5</field>", cron)
        self.assertIn("<field name=\"interval_type\">minutes</field>", cron)

    def test_18_cron_reuses_existing_advisory_lock_path(self):
        config = (ROOT / "models" / "bosta_config.py").read_text()
        action = config.split("def action_sync_bosta_deliveries", 1)[1].split("def action_process_pending_financials", 1)[0]
        cron = config.split("def _cron_sync_due_configs", 1)[1]
        self.assertIn("_try_acquire_sync_lock", action)
        self.assertIn("_release_sync_lock", action)
        self.assertIn("action_sync_bosta_deliveries()", cron)
        self.assertIn("bosta_run_financial_enrichment=True", cron)
        self.assertIn("BostaFinancialEnrichmentService", action)

    def test_19_details_enrichment_is_default_off_and_bounded(self):
        config = (ROOT / "models" / "bosta_config.py").read_text()
        service = (ROOT / "services" / "bosta_financial_enrichment_service.py").read_text()
        self.assertIn("financial_details_enrichment_enabled = fields.Boolean", config)
        self.assertIn("default=False", config.split("financial_details_enrichment_enabled", 1)[1].split(")", 1)[0])
        self.assertIn("financial_details_batch_limit", config)
        self.assertIn("limit=limit", service)
        self.assertIn("timedelta(hours=1)", service)

    def test_20_search_extraction_still_has_zero_implicit_details(self):
        extraction = (ROOT / "services" / "bosta_extraction_service.py").read_text()
        method = extraction.split("def iter_normalized_search_deliveries", 1)[1].split("def get_normalized_delivery_details", 1)[0]
        self.assertNotIn("get_delivery_details", method)
        self.assertNotIn("get_normalized_delivery_details", method)

    def test_21_financial_security_access_is_manager_write_user_read(self):
        csv = (ROOT / "security" / "ir.model.access.csv").read_text()
        self.assertIn("access_bosta_delivery_financial_manager", csv)
        self.assertIn("group_bosta_integration_manager,1,1,0,0", csv)
        self.assertIn("access_bosta_delivery_financial_user", csv)
        self.assertIn("group_bosta_integration_user,1,0,0,0", csv)

    def test_22_company_rules_exist_for_financial_models(self):
        rules = (ROOT / "security" / "bosta_record_rules.xml").read_text()
        self.assertIn("rule_bosta_delivery_financial_company", rules)
        self.assertIn("rule_bosta_delivery_financial_line_company", rules)
        self.assertIn('[("company_id", "in", company_ids)]', rules)

    def test_23_no_financial_pii_or_secret_fields(self):
        source = (ROOT / "models" / "bosta_financial.py").read_text().lower()
        for forbidden in ("api_key", "authorization", "receiver_phone", "receiver_name", "dropoff_first_line", "raw_payload"):
            self.assertNotIn(forbidden, source)

    def test_24_financial_ui_uses_operational_contribution_wording(self):
        views = (ROOT / "views" / "bosta_financial_views.xml").read_text().lower()
        self.assertIn("operational profitability", views)
        self.assertIn("contribution_profit", views)
        self.assertNotIn("company net profit", views)

    def test_25_all_xml_parses(self):
        for path in ROOT.rglob("*.xml"):
            ET.parse(path)

    def test_26_manifest_loads_cron_after_security_and_financial_view(self):
        manifest = ast.literal_eval((ROOT / "__manifest__.py").read_text())
        self.assertIn("data/bosta_cron.xml", manifest["data"])
        self.assertIn("views/bosta_financial_views.xml", manifest["data"])
        self.assertLess(manifest["data"].index("security/bosta_record_rules.xml"), manifest["data"].index("data/bosta_cron.xml"))

    def test_27_financial_finalization_blocks_rewrite(self):
        model = (ROOT / "models" / "bosta_financial.py").read_text()
        service = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn('if financial.financial_status == "finalized":', service)
        self.assertIn("Finalized historical cost snapshots are immutable", model)

    def test_28_manager_overrides_are_audited(self):
        model = (ROOT / "models" / "bosta_financial.py").read_text()
        for token in (
            "revenue_confirmed_at", "revenue_confirmed_by_id", "revenue_override_reason",
            "logistics_confirmed_at", "logistics_confirmed_by_id", "logistics_override_reason",
            "cost_overridden_at", "cost_overridden_by_id", "cost_override_reason",
        ):
            self.assertIn(token, model)

    def test_29_financial_service_does_not_swallow_programming_errors(self):
        source = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertNotIn("except Exception", source)

    def test_30_phase9_runtime_has_no_shopify_or_refund_integration(self):
        phase9 = "\n".join([
            (ROOT / "models" / "bosta_financial.py").read_text().lower(),
            (ROOT / "services" / "bosta_financial_service.py").read_text().lower(),
            (ROOT / "services" / "bosta_financial_enrichment_service.py").read_text().lower(),
        ])
        for forbidden in ("shopify api", "payment gateway", "wallet credit", "credit note", "account.move"):
            self.assertNotIn(forbidden, phase9)

    def test_31_inherited_views_have_no_record_level_groups_id(self):
        violations = []
        for path in ROOT.rglob("*.xml"):
            root = ET.parse(path).getroot()
            for record in root.iter("record"):
                if record.get("model") != "ir.ui.view":
                    continue
                fields = {field.get("name") for field in record.findall("field")}
                if "inherit_id" in fields and "groups_id" in fields:
                    violations.append(f"{path.relative_to(ROOT)}:{record.get('id')}")
        self.assertEqual(violations, [])

    def test_32_finalized_returns_use_immutable_adjustments(self):
        model = (ROOT / "models" / "bosta_financial.py").read_text()
        service = (ROOT / "services" / "bosta_financial_service.py").read_text()
        self.assertIn('_name = "bosta.financial.adjustment"', model)
        self.assertIn("unique(financial_id, restoration_effect_line_id)", model)
        self.assertIn("Finalized financial base snapshots are immutable; use adjustments", model)
        self.assertIn("_process_finalized_adjustments", service)
        self.assertIn('return self._process_finalized_adjustments(financial)', service)
        self.assertIn('return_type == "post_delivery_customer_return" and row.role != "main"', service)

    def test_33_cron_unexpected_failure_logging_is_redacted(self):
        source = (ROOT / "models" / "bosta_config.py").read_text()
        cron = source.split("def _cron_sync_due_configs", 1)[1]
        self.assertNotIn("_logger.exception", cron)
        self.assertIn('_logger.error("Unexpected scheduled Bosta sync failure; sensitive details redacted")', cron)
        self.assertIn('safe_error = "unexpected_scheduled_sync_failure"', cron)

    def test_34_delivery_create_protects_api_financial_sources_and_presence(self):
        source = (ROOT / "models" / "bosta_delivery.py").read_text()
        create = source.split("def create(self, vals_list):", 1)[1].split("def write(self, vals):", 1)[0]
        self.assertIn("_FINANCIAL_API_SOURCE_FIELDS | _FINANCIAL_PRESENCE_FIELDS", create)
        self.assertIn('bosta_delivery_persistence', create)
        for field_name in (
            "cod_amount", "original_cod_amount", "shipment_fees", "shipping_fee",
            "bundle_discount", "opening_package_fee", "bosta_material_fee",
            "price_before_vat", "price_after_vat", "vat_rate", "pricing_currency_code",
        ):
            self.assertIn(f'"{field_name}"', source)

    def test_35_adjustment_company_security_is_registered(self):
        access = (ROOT / "security" / "ir.model.access.csv").read_text()
        rules = (ROOT / "security" / "bosta_record_rules.xml").read_text()
        self.assertIn("model_bosta_financial_adjustment", access)
        self.assertIn("rule_bosta_financial_adjustment_company", rules)
        self.assertIn("model_bosta_financial_adjustment", rules)

    def test_36_inherited_xpath_selectors_do_not_use_string_attribute(self):
        violations = []
        for path in ROOT.rglob("*.xml"):
            root = ET.parse(path).getroot()
            for record in root.iter("record"):
                if record.get("model") != "ir.ui.view":
                    continue
                fields = {field.get("name") for field in record.findall("field")}
                if "inherit_id" not in fields:
                    continue
                arch = next((field for field in record.findall("field") if field.get("name") == "arch"), None)
                if arch is None:
                    continue
                for xpath in arch.iter("xpath"):
                    expr = xpath.get("expr") or ""
                    if "@string" in expr:
                        violations.append(
                            f"{path.relative_to(ROOT)}:{record.get('id')}:{expr}"
                        )
        self.assertEqual(violations, [])

    def test_37_original_financial_compute_is_upgrade_safe_and_company_scoped(self):
        source = (ROOT / "models" / "bosta_return_case.py").read_text()
        compute = source.split("def _compute_original_financial_id", 1)[1].split("_sql_constraints", 1)[0]
        decorator = source.split("def _compute_original_financial_id", 1)[0].rsplit("@api.depends", 1)[1]
        self.assertIn('(\"original_delivery_id\")', decorator)
        self.assertNotIn("original_delivery_id.financial_ids", source)
        self.assertIn('self.env[\"bosta.delivery.financial\"]', compute)
        self.assertIn('(\"company_id\", \"=\", case.company_id.id)', compute)
        self.assertIn('(\"delivery_id\", \"=\", case.original_delivery_id.id)', compute)
        self.assertIn("limit=1", compute)
        self.assertNotIn(".sudo()", compute)

    def test_38_phase9_models_have_no_dotted_api_depends(self):
        violations = []
        for path in sorted((ROOT / "models").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not (
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == "depends"
                    ):
                        continue
                    for argument in decorator.args:
                        if (
                            isinstance(argument, ast.Constant)
                            and isinstance(argument.value, str)
                            and "." in argument.value
                        ):
                            violations.append(
                                f"{path.name}:{node.name}:{argument.value}"
                            )
        self.assertEqual(violations, [])

    def test_39_manifest_metadata_describes_phase9_not_phase2r(self):
        manifest = ast.literal_eval((ROOT / "__manifest__.py").read_text())
        summary = " ".join(manifest["summary"].lower().split())
        description = " ".join(manifest["description"].lower().split())
        for term in ("direct bosta api", "inventory", "returns", "financial", "scheduled sync"):
            self.assertIn(term, summary)
        for term in (
            "direct bosta api",
            "persistent deliveries",
            "lifecycle",
            "product mapping",
            "inventory",
            "restoration",
            "operational contribution snapshots",
            "bosta logistics-fee evidence",
            "scheduled sync",
        ):
            self.assertIn(term, description)
        stale = " ".join((summary, description))
        self.assertNotIn("secure bosta api configuration, client, pagination, and connection testing", stale)
        self.assertNotIn("phase 3+", stale)
        self.assertNotIn("scheduled sync is excluded", stale)

    def test_40_deployable_tree_has_no_development_or_macos_artifacts(self):
        # Runtime imports may legitimately create __pycache__/ and *.pyc next to
        # the tests. Bytecode artifacts are therefore validated on the final ZIP,
        # not against the live source tree while the suite is running.
        violations = []
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            if any(part in {".git", "__macosx"} for part in map(str.lower, relative.parts)):
                violations.append(str(relative))
                continue
            if path.is_file() and path.name == ".DS_Store":
                violations.append(str(relative))
        self.assertEqual(violations, [])

