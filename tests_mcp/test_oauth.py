"""OAuth/MCP boundary tests: the shared suite from ai_connector_core, plus what depends on
this connector's tools and owner check. All identities, grants and data are synthetic."""
import unittest
from unittest.mock import patch

from ai_connector_core.oauth import ConsentError
from ai_connector_core.testing import OAuthSuite
from fakes import BASE, OWNER, FakeSpliit, MemoryGroups, owner_identity
from app import create_app, verify_owner
from config import Config
from store import MemoryStore


class OAuthTests(OAuthSuite, unittest.TestCase):
    def make_store(self):
        return MemoryStore()  # with the change log the tools write

    def make_app(self, store):
        config = Config(BASE, "synthetic-project", OWNER["email"], {})
        self.spliit = FakeSpliit()
        self.groups = MemoryGroups()
        return config, create_app(config, store, owner_identity, self.groups, self.spliit.transport)

    def test_end_to_end_initialize_list_and_call_status_tool(self):
        t = self.tokens()
        init = self.mcp(t["access_token"], "initialize", {"protocolVersion": "2025-11-25",
            "capabilities": {}, "clientInfo": {"name": "synthetic-test", "version": "1"}})
        self.assertEqual(init.status_code, 200, init.text)
        self.assertIn("serverInfo", init.json()["result"])
        r = self.mcp(t["access_token"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({x["name"] for x in r.json()["result"]["tools"]},
                         {"list_groups", "get_balances", "list_expenses", "get_expense", "list_activity", "list_categories",
                          "create_expense", "record_reimbursement", "update_expense", "delete_expense"})
        call = self.mcp(t["access_token"], "tools/call", {"name": "list_groups", "arguments": {}})
        self.assertEqual(call.status_code, 200, call.text)
        data = call.json()["result"]["structuredContent"]
        self.assertEqual(data["owner_url"], BASE + "/owner")

    def test_google_owner_verifier_checks_provider_verified_email_and_freshness(self):
        good = {**OWNER, "email_verified": True, "auth_time": __import__('time').time(),
                "firebase": {"sign_in_provider": "google.com"}}
        with patch("app.id_token_claims", return_value=good):
            self.assertEqual(verify_owner("synthetic", self.config), OWNER)
        for bad in [{**good,"email_verified":False}, {**good,"firebase":{"sign_in_provider":"custom"}},
                    {**good,"auth_time":0}]:
            with patch("app.id_token_claims", return_value=bad):
                with self.assertRaisesRegex(ConsentError, "^owner_google_login_required$"):
                    verify_owner("synthetic", self.config)
        # Only a good, recent sign-in is told that it belongs to someone else.
        with patch("app.id_token_claims", return_value={**good, "email": "stranger@example.com"}):
            with self.assertRaisesRegex(ConsentError, "^not_owner$"):
                verify_owner("synthetic", self.config)
        with patch("app.id_token_claims", return_value={**good, "email": "stranger@example.com",
                                                                      "auth_time": 0}):
            with self.assertRaisesRegex(ConsentError, "^owner_google_login_required$"):
                verify_owner("synthetic", self.config)


if __name__ == "__main__":
    unittest.main()
