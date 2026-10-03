"""The owner's saved Spliit groups, kept in Secret Manager.

Each entry is {"name", "id", "me"}: the name the AI and dashboard use, the group ID from
its link (the only credential Spliit has), and the participant who is the owner. Only
this service's account can read or change the secret. It is read on every call, so a
change in the dashboard takes effect at once.
"""
import json


class SecretGroups:
    def __init__(self, project, secret_id):
        from google.cloud import secretmanager
        self.client = secretmanager.SecretManagerServiceClient()
        self.parent = f"projects/{project}/secrets/{secret_id}"

    def get(self):
        from google.api_core.exceptions import NotFound
        try:
            data = self.client.access_secret_version(name=self.parent + "/versions/latest").payload.data
        except NotFound:
            return []  # nothing saved yet
        return json.loads(data)

    def save(self, groups):
        """Save the whole list, then destroy older versions so only the current one exists."""
        old = list(self.client.list_secret_versions(request={"parent": self.parent, "filter": "state:ENABLED"}))
        self.client.add_secret_version(request={"parent": self.parent,
                                                "payload": {"data": json.dumps(groups).encode()}})
        for version in old:
            self.client.destroy_secret_version(request={"name": version.name})
