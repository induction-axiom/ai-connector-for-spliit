"""The owner's Splitwise API key, kept in Secret Manager.

Only this service's account can read or change the secret. It is read on every
Splitwise call, so a key saved or removed in the dashboard takes effect at once.
"""


class SecretKey:
    def __init__(self, project, secret_id):
        from google.cloud import secretmanager
        self.client = secretmanager.SecretManagerServiceClient()
        self.parent = f"projects/{project}/secrets/{secret_id}"

    def get(self):
        from google.api_core.exceptions import FailedPrecondition, NotFound
        try:
            return self.client.access_secret_version(name=self.parent + "/versions/latest").payload.data.decode()
        except (NotFound, FailedPrecondition):
            # No version yet, or the latest one was destroyed when the key was removed.
            return None

    def versions(self):
        return list(self.client.list_secret_versions(request={"parent": self.parent, "filter": "state:ENABLED"}))

    def replace(self, value):
        """Save a new key, then destroy the older ones so only the current key exists."""
        old = self.versions()
        self.client.add_secret_version(request={"parent": self.parent, "payload": {"data": value.encode()}})
        for version in old:
            self.client.destroy_secret_version(request={"name": version.name})

    def remove(self):
        for version in self.versions():
            self.client.destroy_secret_version(request={"name": version.name})

    def saved_at(self):
        """When the current key was saved, in Unix seconds; None without one."""
        return max((v.create_time.timestamp() for v in self.versions()), default=None)
