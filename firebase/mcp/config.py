"""Explicit deployment configuration. No Spliit group links."""
from dataclasses import dataclass
import json
import os
from urllib.parse import urlsplit

SCOPE = "spliit"


@dataclass(frozen=True)
class Config:
    base_url: str
    project_id: str
    owner_email: str
    firebase_config: dict
    database: str = "(default)"
    test_redirects: tuple[str, ...] = ()
    groups_secret: str = "spliit-groups"
    scope: str = SCOPE

    @property
    def resource(self):
        return self.base_url + "/mcp"

    @classmethod
    def from_env(cls):
        base = os.environ["MCP_BASE_URL"].rstrip("/")
        u = urlsplit(base)
        if u.scheme != "https" or not u.hostname or u.path or u.query or u.fragment or u.username:
            raise ValueError("MCP_BASE_URL must be an HTTPS origin")
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        owner = os.environ["MCP_OWNER_EMAIL"].casefold()
        if "@" not in owner:
            raise ValueError("MCP_OWNER_EMAIL required")
        public = json.loads(os.environ["FIREBASE_WEB_CONFIG"])
        if public.get("projectId") != project or not public.get("apiKey"):
            raise ValueError("Firebase configuration mismatch")
        return cls(base, project, owner, public, os.environ["MCP_AUTH_DATABASE"],
                   groups_secret=os.environ["GROUPS_SECRET_ID"])
