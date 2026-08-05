"""Bosta Dashboard authentication and saved-session restoration."""

import re
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import crypto_service
from .auth_result import AuthResult
from .browser_factory import BrowserFactory
from .dashboard_session_service import DashboardSessionService
from .exceptions import (
    BostaAuthenticationError,
    BostaBlockedError,
    BostaBrowserUnavailableError,
    BostaCaptchaRequiredError,
    BostaDashboardConnectionError,
    BostaDashboardError,
    BostaInvalidCredentialsError,
    BostaLoginPageChangedError,
    BostaOtpRequiredError,
    BostaSessionStateError,
)


class DashboardAuthService:
    """Authenticate without exposing credentials or storage-state contents."""

    _LOGIN_NAMES = re.compile(
        r"^(email|phone|email or phone|mobile|البريد الإلكتروني|رقم الهاتف|"
        r"البريد الإلكتروني أو رقم الهاتف)$",
        re.IGNORECASE,
    )
    _PASSWORD_NAMES = re.compile(r"^(password|كلمة المرور)$", re.IGNORECASE)
    _BUTTON_NAMES = re.compile(
        r"^(login|log in|sign in|تسجيل الدخول|دخول)$", re.IGNORECASE
    )

    _INVALID_PATTERNS = (
        "invalid credentials",
        "incorrect password",
        "wrong password",
        "invalid email",
        "بيانات الدخول غير صحيحة",
        "كلمة المرور غير صحيحة",
    )
    _OTP_PATTERNS = (
        "one-time code",
        "verification code",
        "enter otp",
        "رمز التحقق",
        "رمز لمرة واحدة",
    )
    _CAPTCHA_PATTERNS = ("captcha", "recaptcha", "أنا لست برنامج روبوت")
    _BLOCKED_PATTERNS = (
        "account blocked",
        "too many attempts",
        "temporarily locked",
        "تم حظر الحساب",
        "محاولات كثيرة",
    )

    def __init__(self, config, browser_factory=None, session_service=None):
        self.config = config
        self.browser_factory = browser_factory or BrowserFactory()
        self.session_service = session_service or DashboardSessionService

    def authenticate(self):
        try:
            self._validate_configuration()
            if self.config.encrypted_session_state:
                restored = self._try_existing_session()
                if restored is not None:
                    return restored
            return self._fresh_login(
                clear_existing_session=bool(self.config.encrypted_session_state)
            )
        except BostaDashboardError as exc:
            return self._failure_result(exc)
        except Exception:
            return AuthResult(
                success=False,
                status="unknown_error",
                message="Bosta Dashboard authentication failed safely.",
                clear_session=False,
            )

    def _validate_configuration(self):
        if not self.config.active:
            raise BostaAuthenticationError(
                "The Bosta integration configuration is archived."
            )
        self.config._validate_dashboard_url_value(self.config.dashboard_url)
        if not self.config.dashboard_login:
            raise BostaAuthenticationError("Configure the Bosta Dashboard login first.")
        if not self.config.encrypted_dashboard_password:
            raise BostaAuthenticationError(
                "Configure and save the Bosta Dashboard password first."
            )
        if not crypto_service.is_encryption_configured():
            raise BostaAuthenticationError(
                "Bosta Dashboard encryption is not configured on the server."
            )
        timeout = self.config.browser_timeout_seconds
        if not isinstance(timeout, int) or timeout < 5 or timeout > 120:
            raise BostaAuthenticationError(
                "Browser timeout must be between 5 and 120 seconds."
            )

    def _try_existing_session(self):
        try:
            storage_state = self.session_service.decrypt_storage_state(
                self.config.encrypted_session_state
            )
        except BostaSessionStateError:
            return None

        try:
            with self.browser_factory.open(
                self.config.browser_timeout_seconds,
                storage_state=storage_state,
            ) as resources:
                self._navigate(resources.page)
                if self._is_authenticated(resources.page):
                    return AuthResult(
                        success=True,
                        status="authenticated",
                        message="Bosta Dashboard saved session is authenticated.",
                        used_existing_session=True,
                    )
        except (BostaBrowserUnavailableError, BostaDashboardConnectionError):
            raise
        return None

    def _fresh_login(self, clear_existing_session=False):
        try:
            with self.browser_factory.open(
                self.config.browser_timeout_seconds
            ) as resources:
                page = resources.page
                self._navigate(page)

                if self._is_authenticated(page):
                    encrypted_state = self._capture_encrypted_state(resources.context)
                    return AuthResult(
                        success=True,
                        status="authenticated",
                        message="Bosta Dashboard authentication succeeded.",
                        encrypted_session_state=encrypted_state,
                        attempted_fresh_login=True,
                    )

                self._raise_detected_failure(page)
                login_locator = self._find_login_locator(page)
                password_locator = self._find_password_locator(page)
                submit_locator = self._find_submit_locator(page)
                if not login_locator or not password_locator or not submit_locator:
                    raise BostaLoginPageChangedError()

                password = crypto_service.decrypt_secret(
                    self.config.encrypted_dashboard_password
                )
                try:
                    login_locator.fill(self.config.dashboard_login)
                    password_locator.fill(password)
                    submit_locator.click()
                    self._wait_for_authentication_outcome(page)
                finally:
                    password = None

                self._raise_detected_failure(page)
                if not self._is_authenticated(page):
                    raise BostaLoginPageChangedError(
                        "The Bosta Dashboard did not expose a recognized authentication result."
                    )

                encrypted_state = self._capture_encrypted_state(resources.context)
                return AuthResult(
                    success=True,
                    status="authenticated",
                    message="Bosta Dashboard authentication succeeded.",
                    encrypted_session_state=encrypted_state,
                    attempted_fresh_login=True,
                    clear_session=clear_existing_session,
                )
        except BostaDashboardError as exc:
            result = self._failure_result(exc)
            return AuthResult(
                success=result.success,
                status=result.status,
                message=result.message,
                attempted_fresh_login=True,
                clear_session=clear_existing_session,
            )
        except (PlaywrightError, PlaywrightTimeoutError):
            return AuthResult(
                success=False,
                status="connection_failed",
                message="The Odoo server could not reach the Bosta Dashboard safely.",
                attempted_fresh_login=True,
                clear_session=clear_existing_session,
            )
        except Exception:
            return AuthResult(
                success=False,
                status="unknown_error",
                message="Bosta Dashboard authentication failed safely.",
                attempted_fresh_login=True,
                clear_session=clear_existing_session,
            )

    def _navigate(self, page):
        try:
            response = page.goto(
                self.config.dashboard_url,
                wait_until="domcontentloaded",
            )
            if response is not None and getattr(response, "status", 200) >= 500:
                raise BostaDashboardConnectionError()
        except BostaDashboardConnectionError:
            raise
        except (PlaywrightError, PlaywrightTimeoutError):
            raise BostaDashboardConnectionError() from None

    def _capture_encrypted_state(self, context):
        try:
            storage_state = context.storage_state()
        except PlaywrightError:
            raise BostaSessionStateError(
                "The authenticated browser session could not be saved safely."
            ) from None
        return self.session_service.encrypt_storage_state(storage_state)

    def _wait_for_authentication_outcome(self, page):
        timeout_ms = int(self.config.browser_timeout_seconds * 1000)
        script = """
            () => {
                const text = (document.body?.innerText || '').toLowerCase();
                const path = window.location.pathname || '';
                const password = document.querySelector('input[type="password"]');
                const markers = [
                    'invalid credentials', 'incorrect password', 'wrong password',
                    'verification code', 'one-time code', 'captcha',
                    'too many attempts', 'account blocked',
                    'بيانات الدخول غير صحيحة', 'كلمة المرور غير صحيحة',
                    'رمز التحقق', 'تم حظر الحساب'
                ];
                return path.startsWith('/orders') || !password || markers.some((m) => text.includes(m));
            }
        """
        try:
            page.wait_for_function(script, timeout=timeout_ms)
        except PlaywrightTimeoutError:
            # The final deterministic classification decides the safe status.
            return

    def _is_authenticated(self, page):
        try:
            parsed = urlparse(page.url)
            if parsed.hostname != "business.bosta.co":
                return False
            if self._login_form_is_visible(page):
                return False
            path_signal = parsed.path.startswith("/orders")
            shell_signal = self._visible_count(
                page.locator('a[href^="/orders/"]')
            ) or self._visible_count(
                page.locator('[class^="br-"], [class*=" br-"]')
            )
            return bool(path_signal and shell_signal)
        except Exception:
            return False

    def _login_form_is_visible(self, page):
        password = self._find_password_locator(page)
        return bool(password)

    def _find_login_locator(self, page):
        candidates = [
            page.get_by_label(self._LOGIN_NAMES),
            page.get_by_placeholder(self._LOGIN_NAMES),
            page.locator('input[autocomplete="username"]'),
            page.locator('input[autocomplete="email"]'),
            page.locator('input[autocomplete="tel"]'),
            page.locator('input[type="email"]'),
            page.locator('input[type="tel"]'),
            page.locator('input[name="email"]'),
            page.locator('input[name="phone"]'),
            page.locator('input[name="login"]'),
        ]
        return self._first_visible(candidates)

    def _find_password_locator(self, page):
        candidates = [
            page.get_by_label(self._PASSWORD_NAMES),
            page.get_by_placeholder(self._PASSWORD_NAMES),
            page.locator('input[autocomplete="current-password"]'),
            page.locator('input[type="password"]'),
            page.locator('input[name="password"]'),
        ]
        return self._first_visible(candidates)

    def _find_submit_locator(self, page):
        candidates = [
            page.get_by_role("button", name=self._BUTTON_NAMES),
            page.locator('button[type="submit"]'),
            page.locator('input[type="submit"]'),
        ]
        return self._first_visible(candidates)

    @staticmethod
    def _first_visible(candidates):
        for locator in candidates:
            try:
                if locator.count() < 1:
                    continue
                first = locator.first
                if first.is_visible():
                    return first
            except Exception:
                continue
        return None

    @staticmethod
    def _visible_count(locator):
        try:
            count = locator.count()
            for index in range(count):
                if locator.nth(index).is_visible():
                    return True
        except Exception:
            return False
        return False

    def _raise_detected_failure(self, page):
        text = self._safe_body_text(page)
        if self._has_visible_selector(
            page, 'input[autocomplete="one-time-code"]'
        ) or self._contains_any(text, self._OTP_PATTERNS):
            raise BostaOtpRequiredError()
        if self._has_visible_selector(
            page, 'iframe[src*="captcha" i], [class*="captcha" i]'
        ) or self._contains_any(text, self._CAPTCHA_PATTERNS):
            raise BostaCaptchaRequiredError()
        if self._contains_any(text, self._BLOCKED_PATTERNS):
            raise BostaBlockedError()
        if self._contains_any(text, self._INVALID_PATTERNS):
            raise BostaInvalidCredentialsError()

    @staticmethod
    def _safe_body_text(page):
        try:
            return page.locator("body").inner_text(timeout=1000).lower()
        except Exception:
            return ""

    @staticmethod
    def _has_visible_selector(page, selector):
        try:
            locator = page.locator(selector)
            return any(locator.nth(index).is_visible() for index in range(locator.count()))
        except Exception:
            return False

    @staticmethod
    def _contains_any(text, patterns):
        return any(pattern in text for pattern in patterns)

    @staticmethod
    def _failure_result(exc):
        return AuthResult(
            success=False,
            status=exc.status,
            message=exc.safe_message,
        )
