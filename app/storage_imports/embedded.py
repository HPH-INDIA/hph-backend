"""Best-effort import execution inside a web service; durable state lives in DB."""
import threading
import time

from flask import current_app

from app.extensions import db
from app.storage_imports.services import kind_lock, run_once, cleanup_replaced_files

_guard = threading.Lock()


def _process(app, state):
    with app.app_context():
        try:
            # One import at a time across all web processes, including both types.
            with kind_lock("embedded") as acquired:
                if not acquired:
                    return
                run_once()
                if time.monotonic() >= state["next_cleanup"]:
                    cleanup_replaced_files()
                    state["next_cleanup"] = time.monotonic() + 300
        except Exception:
            # Do not log payloads/provider errors. A later progress request retries
            # startup; process_job persists handled import failures for explicit retry.
            db.session.rollback()
            app.logger.warning("Embedded import processing interrupted; resume from saved progress on the next check.")
        finally:
            db.session.remove()


def kick_import_processing():
    """Return immediately; progress polling also recovers after web restarts."""
    app = current_app._get_current_object()
    if not app.config.get("IMPORT_EMBEDDED_PROCESSING", False):
        return
    with _guard:
        state = app.extensions.setdefault("embedded_imports", {"thread": None, "next_cleanup": 0.0})
        if state["thread"] is not None and state["thread"].is_alive():
            return
        thread = threading.Thread(target=_process, args=(app, state), name="file-import", daemon=True)
        state["thread"] = thread
        try:
            thread.start()
        except RuntimeError:
            state["thread"] = None
            app.logger.warning("Could not start embedded import processing; retry on the next progress check.")
