"""Safe exception hierarchy for Bosta Dashboard authentication."""


class BostaDashboardError(Exception):
    """Base error carrying only a safe user-facing category and message."""

    status = "unknown_error"
    default_message = "Bosta Dashboard authentication could not be completed safely."

    def __init__(self, message=None):
        super().__init__(message or self.default_message)
        self.safe_message = message or self.default_message


class BostaAuthenticationError(BostaDashboardError):
    default_message = "Bosta Dashboard authentication configuration is incomplete."


class BostaInvalidCredentialsError(BostaDashboardError):
    status = "invalid_credentials"
    default_message = "The Bosta Dashboard login or password was rejected."


class BostaOtpRequiredError(BostaDashboardError):
    status = "otp_required"
    default_message = "Bosta Dashboard requires a one-time verification code."


class BostaCaptchaRequiredError(BostaDashboardError):
    status = "captcha_required"
    default_message = "Bosta Dashboard requires CAPTCHA verification."


class BostaBlockedError(BostaDashboardError):
    status = "blocked"
    default_message = "Bosta Dashboard has blocked this authentication attempt."


class BostaSessionExpiredError(BostaDashboardError):
    status = "expired"
    default_message = "The saved Bosta Dashboard session has expired."


class BostaBrowserUnavailableError(BostaDashboardError):
    status = "browser_unavailable"
    default_message = "Chromium is unavailable in the Odoo server environment."


class BostaDashboardConnectionError(BostaDashboardError):
    status = "connection_failed"
    default_message = "The Odoo server could not reach the Bosta Dashboard safely."


class BostaLoginPageChangedError(BostaDashboardError):
    status = "contract_changed"
    default_message = "The Bosta Dashboard login page structure is no longer recognized."


class BostaSessionStateError(BostaDashboardError):
    status = "expired"
    default_message = "The saved Bosta Dashboard session is invalid or unreadable."
