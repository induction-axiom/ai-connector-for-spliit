"""When Splitwise last answered, and its last failure, for the dashboard."""
import logging
import time

RECORD = "health_splitwise"
# Successes are recorded at most this often per instance; failures always are.
QUIET_SECONDS = 300


class Health:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.recorded_ok, self.failing = 0, True

    def __call__(self, code):
        now = self.clock()
        if code is None:
            if not self.failing and now - self.recorded_ok < QUIET_SECONDS:
                return
            self.recorded_ok, self.failing = now, False
            update = {"last_ok_at": now}
        else:
            self.failing = True
            update = {"last_error_code": code, "last_error_at": now}
        try:
            self.store.transact([RECORD], lambda d: (None, {RECORD: {**(d[RECORD] or {}), **update}}))
        except Exception:
            logging.warning("health_record_failed")

    def read(self):
        record = self.store.get(RECORD) or {}
        return {k: record.get(k) for k in ("last_ok_at", "last_error_code", "last_error_at")}
