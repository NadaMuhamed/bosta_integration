from odoo.modules.module import get_manifest
from odoo.tests import TransactionCase


class TestBostaIntegrationBaseline(TransactionCase):

    def test_configuration_model_is_registered(self):
        """The baseline configuration model must be available."""
        config_model = self.env["bosta.integration.config"]

        self.assertEqual(
            config_model._name,
            "bosta.integration.config",
        )

    def test_module_is_independent(self):
        """The new module must not depend on the legacy bosta_orders module."""
        manifest = get_manifest("bosta_integration")
        dependencies = manifest.get("depends", [])

        self.assertEqual(dependencies, ["base"])
        self.assertNotIn("bosta_orders", dependencies)
        self.assertTrue(manifest.get("installable", False))
