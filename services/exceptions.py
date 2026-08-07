"""Safe exception hierarchy for the Bosta HTTP API integration."""


class BostaApiError(Exception):
    """Base error containing only a safe category and user-facing message."""

    status = "unknown_error"
    default_message = "The Bosta API request could not be completed safely."

    def __init__(self, message=None):
        safe_message = message or self.default_message
        super().__init__(safe_message)
        self.safe_message = safe_message


class BostaApiConfigurationError(BostaApiError):
    status = "not_configured"
    default_message = "The Bosta API configuration is incomplete."


class BostaApiAuthenticationError(BostaApiError):
    status = "authentication_failed"
    default_message = "Bosta API authentication failed."


class BostaApiPermissionError(BostaApiError):
    status = "permission_denied"
    default_message = "The Bosta API key does not have permission for this operation."


class BostaApiNotFoundError(BostaApiError):
    status = "contract_error"
    default_message = "The requested Bosta delivery was not found."


class BostaApiRateLimitError(BostaApiError):
    status = "rate_limited"
    default_message = "The Bosta API rate limit was reached."


class BostaApiConnectionError(BostaApiError):
    status = "connection_failed"
    default_message = "The Odoo server could not reach the Bosta API."


class BostaApiTimeoutError(BostaApiError):
    status = "timeout"
    default_message = "The Bosta API request timed out."


class BostaApiServerError(BostaApiError):
    status = "server_error"
    default_message = "The Bosta API is temporarily unavailable."


class BostaApiContractError(BostaApiError):
    status = "contract_error"
    default_message = "The Bosta API returned an unexpected response format."


class BostaApiPaginationError(BostaApiContractError):
    default_message = "Bosta delivery pagination did not progress safely."


class BostaDeliveryNormalizationError(BostaApiContractError):
    """Safe Phase 4 normalization/merge contract failure."""

    default_message = "Bosta delivery data could not be normalized safely."


class BostaPersistenceError(Exception):
    """Base safe Phase 5 persistence error; never embeds payload/PII."""

    default_message = "Bosta delivery data could not be persisted safely."

    def __init__(self, message=None):
        safe_message = message or self.default_message
        super().__init__(safe_message)
        self.safe_message = safe_message


class BostaPersistenceDataError(BostaPersistenceError):
    default_message = "Normalized Bosta delivery data is invalid for persistence."


class BostaPersistenceIdentityConflict(BostaPersistenceError):
    default_message = "Conflicting Bosta delivery identities"
