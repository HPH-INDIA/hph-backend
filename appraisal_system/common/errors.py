"""HTTP errors raised by service code.

Each error is rendered as ``{"data", "message", "success": false, "code"}`` with its ``status``.
"""


class HTTPException(Exception):
    """Base class for errors that map to an HTTP response.

    Parameters
    ----------
    message : str
        Human-readable reason returned to the caller.
    data : dict, optional, default = None
        Structured detail, e.g. per-field validation errors.
    """

    code = "UNKNOWN"
    status = 500

    def __init__(self, message, data=None):
        super().__init__(message)
        self.message = message
        self.data = data

    def to_body(self):
        return {"data": self.data, "message": self.message, "success": False, "code": self.code}


class InvalidArgument(HTTPException):
    """The request is malformed regardless of system state."""

    code = "INVALID_ARGUMENT"
    status = 400


class FailedPrecondition(HTTPException):
    """The request is valid but the resource is not in a state that allows it."""

    code = "FAILED_PRECONDITION"
    status = 400


class AlreadyExists(HTTPException):
    """The resource, or a conflicting one, already exists."""

    code = "ALREADY_EXISTS"
    status = 400


class Unauthenticated(HTTPException):
    """The caller could not be identified."""

    code = "UNAUTHENTICATED"
    status = 401


class PermissionDenied(HTTPException):
    """The caller is identified but not allowed to do this."""

    code = "PERMISSION_DENIED"
    status = 403


class NotFound(HTTPException):
    """The resource does not exist or is not visible to the caller."""

    code = "NOT_FOUND"
    status = 404


class Internal(HTTPException):
    """An unexpected server-side failure."""

    code = "INTERNAL"
    status = 500


class Unavailable(HTTPException):
    """A service this request depends on could not be reached."""

    code = "UNAVAILABLE"
    status = 503


class UpstreamError(HTTPException):
    """A 4xx answer from another HPH service, carrying that service's status and code.

    Parameters
    ----------
    status : int
        HTTP status the upstream service returned.
    code : str
        Error code from the upstream body, or ``"UPSTREAM_ERROR"`` when absent.
    message : str
        Message from the upstream body.
    data : dict, optional, default = None
        Upstream error detail.
    """

    def __init__(self, status, code, message, data=None):
        super().__init__(message, data)
        self.status = status
        self.code = code or "UPSTREAM_ERROR"
