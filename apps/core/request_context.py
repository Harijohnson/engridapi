"""Per-request context shared with logging (no Django imports, so logging can use it early)."""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
