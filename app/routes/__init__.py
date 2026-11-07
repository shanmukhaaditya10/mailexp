"""HTTP routers. Each module owns one area of the API surface."""
from fastapi import HTTPException


def service_error(exc):
    """Map a service-layer exception to an HTTPException using its `.status`."""
    status = getattr(exc, "status", None) or 400
    return HTTPException(status_code=status, detail=str(exc))
