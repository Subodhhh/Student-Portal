"""Which Institute service is executing the current request/job. Set by the app factory middleware (or `service_context` for
workers); read by the session factory (ownership guard) and the audit recorder."""
import contextlib
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("institute_service", default=None)


def current_service() -> str | None:
    return _current.get()


@contextlib.contextmanager
def service_context(name: str):
    token = _current.set(name)
    try:
        yield
    finally:
        _current.reset(token)
