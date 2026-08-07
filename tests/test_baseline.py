from pathlib import Path

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase


OBSOLETE_COLUMNS = (
    "dashboard_url",
    "dashboard_login",
    "encrypted_dashboard_password",
    "dashboard_password_configured",
    "credentials_updated_at",
    "credentials_updated_by",
    "encrypted_session_state",
    "session_configured",
    "session_status",
    "last_login_attempt_at",
    "last_successful_login_at",
    "last_session_validation_at",
    "last_auth_error",
    "browser_timeout_seconds",
)


class TestBostaIntegrationBaseline(TransactionCase):
    def test_configuration_model_is_registered(self):
        self.assertEqual(self.env["bosta.integration.config"]._name, "bosta.integration.config")

    def test_manifest_remains_independent_after_phase3_version_bump(self):
        manifest = get_manifest("bosta_integration")
        self.assertEqual(manifest["version"], "18.0.6.0.0")
        self.assertEqual(manifest.get("depends"), ["base"])
        self.assertTrue(manifest.get("installable"))
        self.assertFalse(
            manifest.get("external_dependencies"),
            "The module must not declare external dependencies",
        )
        self.assertNotIn("bosta_orders", manifest.get("depends", []))

    def test_no_obsolete_browser_runtime_references(self):
        module_path = Path(get_module_path("bosta_integration"))
        runtime_files = [
            module_path / "__init__.py",
            module_path / "__manifest__.py",
            *sorted((module_path / "models").glob("*.py")),
            *sorted((module_path / "services").glob("*.py")),
        ]
        forbidden = [
            "playwright", "chromium", "DashboardAuthService", "DashboardSessionService",
            "BrowserFactory", "encrypted_dashboard_password", "encrypted_session_state",
            "dashboard_password_input", "action_test_dashboard_login", "action_reset_dashboard_session",
        ]
        rendered = "\n".join(path.read_text(encoding="utf-8").lower() for path in runtime_files)
        for item in forbidden:
            self.assertNotIn(item.lower(), rendered)

    def test_obsolete_files_do_not_exist(self):
        module_path = Path(get_module_path("bosta_integration"))
        obsolete = (
            "services/auth_result.py",
            "services/browser_factory.py",
            "services/crypto_service.py",
            "services/dashboard_auth_service.py",
            "services/dashboard_session_service.py",
            "tests/test_browser_factory.py",
            "tests/test_crypto_service.py",
            "tests/test_dashboard_auth_service.py",
            "tests/test_dashboard_session_service.py",
            "tests/test_authentication_actions.py",
        )
        for relative_path in obsolete:
            with self.subTest(path=relative_path):
                self.assertFalse((module_path / relative_path).exists())

    def test_requirements_manifest_and_runtime_have_no_browser_crypto_dependencies(self):
        module_path = Path(get_module_path("bosta_integration"))
        requirements = (module_path / "requirements.txt").read_text(encoding="utf-8").lower()
        active_requirements = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertFalse(any("playwright" in line for line in active_requirements))
        self.assertFalse(any("cryptography" in line for line in active_requirements))

        manifest_text = (module_path / "__manifest__.py").read_text(encoding="utf-8").lower()
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for directory in ("models", "services")
            for path in sorted((module_path / directory).glob("*.py"))
        )
        self.assertNotIn("playwright", manifest_text + runtime_text)
        self.assertNotIn("cryptography", manifest_text + runtime_text)

    def test_deleted_browser_test_modules_are_not_imported(self):
        module_path = Path(get_module_path("bosta_integration"))
        tests_init = (module_path / "tests" / "__init__.py").read_text(encoding="utf-8")
        for module_name in (
            "test_browser_factory", "test_crypto_service", "test_dashboard_auth_service",
            "test_dashboard_session_service", "test_authentication_actions",
        ):
            self.assertNotIn(module_name, tests_init)

    def test_phase_2r_migration_path_and_schema_cleanup(self):
        module_path = Path(get_module_path("bosta_integration"))
        migration = module_path / "migrations" / "18.0.4.0.0" / "post-migration.py"
        self.assertTrue(migration.exists())

        self.cr.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'bosta_integration_config'
            """
        )
        columns = {row[0] for row in self.cr.fetchall()}
        for column in OBSOLETE_COLUMNS:
            with self.subTest(column=column):
                self.assertNotIn(column, columns)
