class SISError(Exception):
    """Base class: messages are safe to show to the user."""


class AuthError(SISError):
    pass


class PermissionDenied(SISError):
    pass


class ValidationFailed(SISError):
    pass


class NotFound(SISError):
    pass
