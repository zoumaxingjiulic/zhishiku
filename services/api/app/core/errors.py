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

