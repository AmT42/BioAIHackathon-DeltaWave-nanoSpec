from __future__ import annotations

import logging
import os
import threading
import time


logger = logging.getLogger(__name__)
_RELOAD_LOCK = threading.Lock()
_RELOAD_SCHEDULED = False


def schedule_process_reload(*, exit_code: int = 75, delay_ms: int = 350) -> bool:
    global _RELOAD_SCHEDULED
    with _RELOAD_LOCK:
        if _RELOAD_SCHEDULED:
            return False
        _RELOAD_SCHEDULED = True

    sleep_s = max(0, int(delay_ms)) / 1000.0
    code = int(exit_code)

    def _worker() -> None:
        try:
            if sleep_s:
                time.sleep(sleep_s)
            logger.warning("Controlled reload requested; exiting process with code %s.", code)
            os._exit(code)
        except Exception:
            # Fallback hard exit path in the unlikely case logging/sleep fails.
            os._exit(code)

    thread = threading.Thread(target=_worker, name="controlled-reload-exit", daemon=True)
    thread.start()
    return True

