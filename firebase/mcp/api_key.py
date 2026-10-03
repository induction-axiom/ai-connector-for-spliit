"""The owner's Splitwise API key, kept in Secret Manager.

Only this service's account can read or change the secret. The key is cached for a minute in
memory; a 401 from Splitwise or a change from the dashboard reads it again.
"""
import threading
import time


class SecretKey:
    def __init__(self, project, secret_id, ttl=60, clock=time.monotonic):
        from google.cloud import secretmanager
        self.client = secretmanager.SecretManagerServiceClient()
        self.parent = f"projects/{project}/secrets/{secret_id}"
        self.ttl, self.clock = ttl, clock
        self.lock = threading.Lock()
        self.value, self.loaded_at = None, None

    def get(self):
        with self.lock:
            if self.loaded_at is not None and self.clock() - self.loaded_at < self.ttl:
                return self.value
        from google.api_core.exceptions import FailedPrecondition, NotFound
        try:
            response = self.client.access_secret_version(name=self.parent + "/versions/latest",
                                                         timeout=10)
            value = response.payload.data.decode() or None
        except (NotFound, FailedPrecondition):
            # No version yet, or the latest one was destroyed when the key was removed.
            value = None
        with self.lock:
            self.value, self.loaded_at = value, self.clock()
        return value

    def invalidate(self):
        with self.lock:
            self.loaded_at = None

    def _versions(self):
        return list(self.client.list_secret_versions(
            request={"parent": self.parent, "filter": "state:ENABLED OR state:DISABLED"}, timeout=10))

    def replace(self, value):
        """Save a new key, then destroy every older one so only the current key exists."""
        added = self.client.add_secret_version(
            request={"parent": self.parent, "payload": {"data": value.encode()}}, timeout=10)
        for version in self._versions():
            if version.name != added.name:
                self.client.destroy_secret_version(request={"name": version.name}, timeout=10)
        with self.lock:
            self.value, self.loaded_at = value, self.clock()

    def remove(self):
        for version in self._versions():
            self.client.destroy_secret_version(request={"name": version.name}, timeout=10)
        with self.lock:
            self.value, self.loaded_at = None, self.clock()

    def saved_at(self):
        """When the current key was saved, in Unix seconds; None without one."""
        times = [v.create_time.timestamp() for v in self._versions() if v.state.name == "ENABLED"]
        return max(times) if times else None
