"""Framework-independent errors raised by application services."""


class ApplicationError(Exception):
    """Base class for errors that the HTTP boundary can translate."""


class AuthenticationError(ApplicationError):
    """The caller could not be authenticated."""


class AuthorizationError(ApplicationError):
    """The authenticated caller is not allowed to perform an action."""


class NotFoundError(ApplicationError):
    """A requested business resource does not exist."""


class ConflictError(ApplicationError):
    """The requested state transition conflicts with current state."""


class ValidationError(ApplicationError):
    """The request violates a business validation rule."""


class RateLimitError(ApplicationError):
    """The caller exceeded a bounded concurrency or request allowance."""


class ServiceUnavailableError(ApplicationError):
    """A configured runtime dependency cannot currently serve the request."""


class CompensationRequiredError(ApplicationError):
    """A cross-store operation needs operator reconciliation."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"操作状态需要人工核对，追踪号：{operation_id}")
