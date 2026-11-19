"""Problem Details responses (RFC 9457) with a stable code and the trace id (agent spec 12.1)."""

from fastapi.responses import JSONResponse

TITLES = {
    401: "Not signed in",
    403: "Not permitted",
    404: "Not found",
    409: "Conflict",
    422: "Invalid request",
    429: "Too many requests",
    503: "Temporarily unavailable",
    504: "Timed out",
}


def problem(
    status: int, code: str, detail: str, trace_id: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {
            "type": "about:blank",
            "title": TITLES.get(status, "Error"),
            "status": status,
            "code": code,
            "detail": detail,
            "trace_id": trace_id,
        },
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )
