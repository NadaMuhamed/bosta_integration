from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from odoo.tests import TransactionCase, tagged

from ..services import crypto_service
from ..services.auth_result import AuthResult
from ..services.dashboard_auth_service import DashboardAuthService
from ..services.dashboard_session_service import DashboardSessionService
from ..services.exceptions import (
    BostaBlockedError,
    BostaBrowserUnavailableError,
    BostaCaptchaRequiredError,
    BostaDashboardConnectionError,
    BostaInvalidCredentialsError,
    BostaLoginPageChangedError,
    BostaOtpRequiredError,
)


class QueueBrowserFactory:
    def __init__(self, resources=None, error=None):
        self.resources = list(resources or [])
        self.error = error
        self.calls = []

    @contextmanager
    def open(self, timeout_seconds, storage_state=None):
        self.calls.append((timeout_seconds, storage_state))
        if self.error:
            raise self.error
        yield self.resources.pop(0)


@tagged("post_install", "-at_install")
class TestDashboardAuthService(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = Fernet.generate_key().decode("ascii")
        cls.password = "phase2-private-password"
        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: cls.key},
            clear=True,
        ):
            cls.encrypted_password = crypto_service.encrypt_secret(cls.password)
            cls.encrypted_state = DashboardSessionService.encrypt_storage_state(
                {"cookies": [], "origins": []}
            )

    def _config(self, **overrides):
        values = {
            "active": True,
            "dashboard_url": "https://business.bosta.co/orders",
            "dashboard_login": "manager@example.com",
            "encrypted_dashboard_password": self.encrypted_password,
            "encrypted_session_state": False,
            "browser_timeout_seconds": 30,
            "_validate_dashboard_url_value": MagicMock(),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _resources(self):
        return SimpleNamespace(page=MagicMock(), context=MagicMock())

    def _key_environment(self):
        return patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: self.key},
            clear=True,
        )

    def test_missing_dashboard_login_prevents_browser_launch(self):
        factory = QueueBrowserFactory([self._resources()])
        with self._key_environment():
            result = DashboardAuthService(
                self._config(dashboard_login=False), factory
            ).authenticate()
        self.assertFalse(result.success)
        self.assertEqual(result.status, "unknown_error")
        self.assertFalse(factory.calls)

    def test_missing_configured_password_prevents_browser_launch(self):
        factory = QueueBrowserFactory([self._resources()])
        with self._key_environment():
            result = DashboardAuthService(
                self._config(encrypted_dashboard_password=False), factory
            ).authenticate()
        self.assertFalse(result.success)
        self.assertFalse(factory.calls)

    def test_missing_encryption_key_prevents_browser_launch(self):
        factory = QueueBrowserFactory([self._resources()])
        with patch.dict("os.environ", {}, clear=True):
            result = DashboardAuthService(self._config(), factory).authenticate()
        self.assertFalse(result.success)
        self.assertFalse(factory.calls)

    def test_valid_stored_session_is_restored(self):
        resources = self._resources()
        factory = QueueBrowserFactory([resources])
        service = DashboardAuthService(
            self._config(encrypted_session_state=self.encrypted_state), factory
        )
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(service, "_is_authenticated", return_value=True):
            result = service.authenticate()
        self.assertTrue(result.success)
        self.assertTrue(result.used_existing_session)
        self.assertEqual(factory.calls[0][1], {"cookies": [], "origins": []})

    def test_valid_restored_session_does_not_decrypt_password(self):
        resources = self._resources()
        service = DashboardAuthService(
            self._config(encrypted_session_state=self.encrypted_state),
            QueueBrowserFactory([resources]),
        )
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(service, "_is_authenticated", return_value=True), patch.object(
            crypto_service, "decrypt_secret", wraps=crypto_service.decrypt_secret
        ) as decrypt_call:
            result = service.authenticate()
        self.assertTrue(result.success)
        # One decryption is for storage state; no second decryption for the password.
        self.assertEqual(decrypt_call.call_count, 1)

    def test_expired_stored_session_performs_exactly_one_fresh_login(self):
        restored = self._resources()
        fresh = self._resources()
        factory = QueueBrowserFactory([restored, fresh])
        service = DashboardAuthService(
            self._config(encrypted_session_state=self.encrypted_state), factory
        )
        login = MagicMock()
        password = MagicMock()
        submit = MagicMock()
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(
            service, "_is_authenticated", side_effect=[False, False, True]
        ), patch.object(service, "_raise_detected_failure"), patch.object(
            service, "_find_login_locator", return_value=login
        ), patch.object(
            service, "_find_password_locator", return_value=password
        ), patch.object(
            service, "_find_submit_locator", return_value=submit
        ), patch.object(
            service, "_wait_for_authentication_outcome"
        ), patch.object(
            service, "_capture_encrypted_state", return_value="new-ciphertext"
        ):
            result = service.authenticate()
        self.assertTrue(result.success)
        self.assertEqual(len(factory.calls), 2)
        submit.click.assert_called_once_with()
        self.assertEqual(result.encrypted_session_state, "new-ciphertext")

    def test_successful_fresh_login_fills_credentials_once(self):
        resources = self._resources()
        service = DashboardAuthService(self._config(), QueueBrowserFactory([resources]))
        login = MagicMock()
        password = MagicMock()
        submit = MagicMock()
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(
            service, "_is_authenticated", side_effect=[False, True]
        ), patch.object(service, "_raise_detected_failure"), patch.object(
            service, "_find_login_locator", return_value=login
        ), patch.object(
            service, "_find_password_locator", return_value=password
        ), patch.object(
            service, "_find_submit_locator", return_value=submit
        ), patch.object(
            service, "_wait_for_authentication_outcome"
        ), patch.object(
            service, "_capture_encrypted_state", return_value="encrypted-state"
        ):
            result = service.authenticate()
        self.assertTrue(result.success)
        login.fill.assert_called_once_with("manager@example.com")
        password.fill.assert_called_once_with(self.password)
        submit.click.assert_called_once_with()
        self.assertTrue(result.attempted_fresh_login)

    def test_changed_login_contract_stops_without_filling_arbitrary_controls(self):
        resources = self._resources()
        service = DashboardAuthService(self._config(), QueueBrowserFactory([resources]))
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(service, "_is_authenticated", return_value=False), patch.object(
            service, "_raise_detected_failure"
        ), patch.object(service, "_find_login_locator", return_value=None):
            result = service.authenticate()
        self.assertFalse(result.success)
        self.assertEqual(result.status, "contract_changed")

    def _assert_detected_status(self, error, expected_status):
        resources = self._resources()
        service = DashboardAuthService(self._config(), QueueBrowserFactory([resources]))
        with self._key_environment(), patch.object(
            service, "_navigate"
        ), patch.object(service, "_is_authenticated", return_value=False), patch.object(
            service, "_raise_detected_failure", side_effect=error
        ):
            result = service.authenticate()
        self.assertFalse(result.success)
        self.assertEqual(result.status, expected_status)
        self.assertTrue(result.attempted_fresh_login)

    def test_invalid_credentials_set_safe_status_and_do_not_retry(self):
        self._assert_detected_status(
            BostaInvalidCredentialsError(), "invalid_credentials"
        )

    def test_otp_detection_sets_status_and_stops(self):
        self._assert_detected_status(BostaOtpRequiredError(), "otp_required")

    def test_captcha_detection_sets_status_and_stops(self):
        self._assert_detected_status(
            BostaCaptchaRequiredError(), "captcha_required"
        )

    def test_blocked_detection_sets_status_and_stops(self):
        self._assert_detected_status(BostaBlockedError(), "blocked")

    def test_missing_chromium_sets_browser_unavailable(self):
        with self._key_environment():
            result = DashboardAuthService(
                self._config(),
                QueueBrowserFactory(error=BostaBrowserUnavailableError()),
            ).authenticate()
        self.assertEqual(result.status, "browser_unavailable")

    def test_connection_failure_sets_connection_failed(self):
        service = DashboardAuthService(
            self._config(), QueueBrowserFactory([self._resources()])
        )
        with self._key_environment(), patch.object(
            service, "_navigate", side_effect=BostaDashboardConnectionError()
        ):
            result = service.authenticate()
        self.assertEqual(result.status, "connection_failed")

    def test_unknown_exception_sets_unknown_error_without_secret(self):
        service = DashboardAuthService(
            self._config(), QueueBrowserFactory([self._resources()])
        )
        with self._key_environment(), patch.object(
            service, "_navigate", side_effect=RuntimeError(self.password)
        ):
            result = service.authenticate()
        self.assertEqual(result.status, "unknown_error")
        self.assertNotIn(self.password, result.message)

    def test_failure_result_never_contains_password_or_storage_state(self):
        secret_state = '{"cookies":[{"value":"secret-cookie"}]}'
        result = DashboardAuthService._failure_result(
            BostaLoginPageChangedError()
        )
        rendered = repr(result)
        self.assertNotIn(self.password, rendered)
        self.assertNotIn(secret_state, rendered)

    def test_no_real_bosta_connection_occurs_in_automated_service_tests(self):
        factory = QueueBrowserFactory([self._resources()])
        service = DashboardAuthService(self._config(), factory)
        with self._key_environment(), patch.object(
            service, "_navigate"
        ) as navigate, patch.object(
            service, "_is_authenticated", return_value=True
        ), patch.object(
            service, "_capture_encrypted_state", return_value="encrypted"
        ):
            result = service.authenticate()
        self.assertTrue(result.success)
        navigate.assert_called_once()
