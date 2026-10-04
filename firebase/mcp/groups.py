"""The owner's saved Spliit groups, kept in Secret Manager.

Each entry is {"name", "id", "me"}: the name the AI and dashboard use, kept in step with
Spliit's when the group is renamed there, the group ID from its link (the only credential Spliit has), and the participant who is the owner. Only
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


def sync_names(store, saved, live):
    """Take each group's name from Spliit as it is now, so the AI and the dashboard use the
    name its members see after a rename. live maps a group ID to its Spliit group, or None.
    A name another saved group already has is not taken, since the AI names groups. Saves
    only when a name changed, and returns the groups as saved."""
    taken = {g["name"].casefold() for g in saved}
    updated = []
    for entry in saved:
        name = (live.get(entry["id"]) or {}).get("name")
        if name and name != entry["name"] and (name.casefold() == entry["name"].casefold()
                                               or name.casefold() not in taken):
            taken = (taken - {entry["name"].casefold()}) | {name.casefold()}
            entry = {**entry, "name": name}
        updated.append(entry)
    if updated != saved:
        store.save(updated)
    return updated
