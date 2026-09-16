"""Shared Flask-Limiter instance.

Created in its own module (not inside dashboard.py) so every blueprint file
can `from rate_limit import limiter` and decorate its own routes with
`@limiter.limit(...)` at import time, before dashboard.py's Flask `app`
object exists. dashboard.py calls `limiter.init_app(app)` once the app is
created.

Storage is in-memory (the default here — no Redis/new infrastructure). This
comes with one real caveat for this project specifically: production runs
gunicorn with `--workers 2` (see Procfile). In-memory storage is per
*process*, not per-thread, so the 4 threads within a worker share one
counter (no extra multiplication there), but the 2 worker processes each
keep an independent counter. A client can therefore land on either worker
across requests, so the effective real-world limit is roughly 2x the
configured number, not exactly the configured number. If that slack ever
matters (sustained, coordinated abuse rather than casual crawling), the fix
is a shared external store — Redis via `storage_uri="redis://..."` — not a
code change to this module beyond that one URI.

RATELIMIT_ENABLED is a genuine Flask-Limiter config key, read once during
init_app(). conftest.py defaults it to "false" for the whole test session
(rate limits are per-IP/in-memory and would otherwise make unrelated tests
flaky depending on run order), and the one test that verifies enforcement
flips `limiter.enabled` directly at runtime instead of relying on that
config re-read (which only happens once, at init_app time).
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour"],
    storage_uri="memory://",
)
