from pathlib import Path

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase


class TestBostaIntegrationBaseline(TransactionCase):

    def test_configuration_model_is_registered(self):
        """The independent configuration model must remain available."""
        config_model = self.env["bosta.integration.config"]
        self.assertEqual(config_model._name, "bosta.integration.config")

    def test_module_is_independent(self):
        """Runtime code must not depend on or import the legacy module."""
        manifest = get_manifest("bosta_integration")
        dependencies = manifest.get("depends", [])

        self.assertEqual(dependencies, ["base"])
        legacy_module = "bosta_" + "orders"
        self.assertNotIn(legacy_module, dependencies)
        self.assertTrue(manifest.get("installable", False))

        module_path = Path(get_module_path("bosta_integration"))
        runtime_files = [
            module_path / "__init__.py",
            module_path / "__manifest__.py",
            *sorted((module_path / "models").glob("*.py")),
            *sorted((module_path / "services").glob("*.py")),
        ]
        forbidden_import = "odoo.addons." + legacy_module
        for file_path in runtime_files:
            self.assertNotIn(
                forbidden_import,
                file_path.read_text(encoding="utf-8"),
                msg=f"Legacy import found in {file_path.name}",
            )
