class MuseRouterError(Exception):
    """Base exception for the whole system."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "server_error", code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code

    def to_openai_error(self) -> dict:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": None,
                "code": self.code,
            }
        }


class UpstreamError(MuseRouterError):
    """Error raised while talking to muse.ai."""


class AccountAuthExpiredError(UpstreamError):
    def __init__(self, message: str = "Account authentication expired."):
        super().__init__(message, status_code=401, error_type="authentication_error", code="account_auth_expired")


class AccountQuotaExceededError(UpstreamError):
    def __init__(self, message: str = "Account quota exceeded / rate limited."):
        super().__init__(message, status_code=429, error_type="rate_limit_error", code="account_quota_exceeded")


class UpstreamTransientError(UpstreamError):
    def __init__(self, message: str = "Upstream transient error."):
        super().__init__(message, status_code=502, error_type="server_error", code="upstream_transient")


class NoAvailableAccountError(MuseRouterError):
    def __init__(self, message: str = "No available muse.ai accounts in pool (all cooling down or busy)."):
        super().__init__(message, status_code=503, error_type="service_unavailable", code="no_available_account")
