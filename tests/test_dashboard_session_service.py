import json
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.tests import TransactionCase, tagged

from ..services import crypto_service
from ..services.dashboard_session_service import DashboardSessionService
from ..services.exceptions import BostaSessionStateError


@tagged("post_install", "-at_install")
class TestDashboardSessionService(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = Fernet.generate_key().decode("ascii")
        cls.state = {
            "cookies": [
                {
                    "name": "session",
                    "value": "opaque-cookie-value",
                    "domain": "business.bosta.co",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ],
            "origins": [
                {
                    "origin": "https://business.bosta.co",
                    "localStorage": [{"name": "locale", "value": "en"}],
                }
            ],
        }

    def _key_environment(self):
        return patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: self.key},
            clear=True,
        )

    def test_validate_storage_state_accepts_playwright_shape(self):
        self.assertEqual(
            DashboardSessionService.validate_storage_state(self.state),
            self.state,
        )

    def test_serialize_storage_state_is_deterministic(self):
        first = DashboardSessionService.serialize_storage_state(self.state)
        reversed_state = {"origins": self.state["origins"], "cookies": self.state["cookies"]}
        second = DashboardSessionService.serialize_storage_state(reversed_state)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), self.state)

    def test_session_state_encryption_stores_ciphertext_not_json(self):
        raw_json = DashboardSessionService.serialize_storage_state(self.state)
        with self._key_environment():
            encrypted = DashboardSessionService.encrypt_storage_state(self.state)
        self.assertNotEqual(encrypted, raw_json)
        self.assertNotIn("opaque-cookie-value", encrypted)
        self.assertNotIn("business.bosta.co", encrypted)

    def test_session_state_decryption_returns_original_state(self):
        with self._key_environment():
            encrypted = DashboardSessionService.encrypt_storage_state(self.state)
            decrypted = DashboardSessionService.decrypt_storage_state(encrypted)
        self.assertEqual(decrypted, self.state)

    def test_invalid_storage_state_json_fails_safely(self):
        with self._key_environment():
            encrypted = crypto_service.encrypt_secret("not-json")
            with self.assertRaises(BostaSessionStateError):
                DashboardSessionService.decrypt_storage_state(encrypted)

    def test_invalid_storage_state_top_level_type_fails_safely(self):
        with self.assertRaises(BostaSessionStateError):
            DashboardSessionService.validate_storage_state([])

    def test_invalid_storage_state_structure_fails_safely(self):
        invalid_values = [
            {"cookies": {}, "origins": []},
            {"cookies": [], "origins": {}},
            {"cookies": [{"name": "x"}], "origins": []},
            {"cookies": [], "origins": [{"localStorage": []}]},
            {"cookies": [], "origins": [], "unexpected": True},
        ]
        for value in invalid_values:
            with self.assertRaises(BostaSessionStateError):
                DashboardSessionService.validate_storage_state(value)

    def test_storage_state_json_and_cookies_are_absent_from_errors(self):
        raw = json.dumps(self.state)
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(BostaSessionStateError) as caught:
                DashboardSessionService.encrypt_storage_state(self.state)
        message = str(caught.exception)
        self.assertNotIn(raw, message)
        self.assertNotIn("opaque-cookie-value", message)

    def test_session_service_does_not_emit_logs(self):
        with self._key_environment(), patch("logging.Logger._log") as log_call:
            encrypted = DashboardSessionService.encrypt_storage_state(self.state)
            DashboardSessionService.decrypt_storage_state(encrypted)
        self.assertFalse(log_call.called)
