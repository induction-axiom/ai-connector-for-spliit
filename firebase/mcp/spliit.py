"""Spliit's tRPC API, the one its own web app uses, and the one place that knows its quirks.

It is not a published API. Procedure names and inputs follow Spliit's source at the commit
named in tests_mcp/spliit_api.json, and a test checks every request against it. Requests
send plain JSON inside {"json": ...}; answers are superjson, of which we read "json".
Amounts are integers in the currency's minor unit (cents). A group ID is the only
credential Spliit has: anyone holding it can read and change the group.
"""
from decimal import Decimal
import json

import httpx

SPLIIT = "https://spliit.app"
# Where Spliit's own expense form gets exchange rates: the European Central Bank's, per day.
RATES = "https://api.frankfurter.dev/v1/"
# Currencies Spliit offers whose minor unit is not a hundredth (its src/lib/currency-data.json).
NO_DECIMALS = {"JPY", "HUF", "ISK", "IDR", "KRW", "VND", "COP"}


class SpliitError(Exception):
    """code is ours and stable; detail is Spliit's own text, untrusted."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail


class Spliit:
    """on_failure(code) hears each failure that is about Spliit itself, so the dashboard
    can show the last one."""

    def __init__(self, on_failure, transport=None):
        self.on_failure = on_failure
        self.http = httpx.Client(base_url=SPLIIT + "/api/trpc/", transport=transport, timeout=20)
        self.rates = httpx.Client(base_url=RATES, transport=transport, timeout=10)

    def call(self, method, procedure, payload=None):
        try:
            return self._call(method, procedure, payload)
        except SpliitError as error:
            # A rejected request is about its arguments, not about Spliit.
            if error.code == "spliit_unavailable":
                self.on_failure(error.code)
            raise

    def _call(self, method, procedure, payload):
        wrapped = json.dumps({"json": payload})
        try:
            if method == "GET":
                response = self.http.get(procedure, params={"input": wrapped} if payload else None)
            else:
                response = self.http.post(procedure, content=wrapped,
                                          headers={"Content-Type": "application/json"})
        except httpx.HTTPError:
            raise SpliitError("spliit_unavailable") from None
        if response.status_code >= 500:
            raise SpliitError("spliit_unavailable")
        if response.status_code == 404:
            raise SpliitError("not_found")
        if response.status_code >= 400:
            raise SpliitError("request_rejected", response.json()["error"]["json"]["message"])
        return response.json()["result"]["data"]["json"]

    # ---------- Reads ----------

    def group(self, group_id):
        """The group with its participants; None if no group has this ID."""
        return self.call("GET", "groups.get", {"groupId": group_id})["group"]

    def categories(self):
        return self.call("GET", "categories.list")["categories"]

    def expenses(self, group_id, offset, limit, title_contains=None):
        payload = {"groupId": group_id, "cursor": offset, "limit": limit}
        if title_contains:
            payload["filter"] = title_contains
        return self.call("GET", "groups.expenses.list", payload)

    def expense(self, group_id, expense_id):
        """Spliit finds an expense by its ID alone, whatever group is asked, and deletes and
        updates the same way: check it is in this group before touching it."""
        expense = self.call("GET", "groups.expenses.get", {"groupId": group_id, "expenseId": expense_id})["expense"]
        if expense["groupId"] != group_id:
            raise SpliitError("not_found")
        return expense

    def balances(self, group_id):
        return self.call("GET", "groups.balances.list", {"groupId": group_id})

    def exchange_rate(self, day, base, target):
        """How much 1 base is in target on day, as Spliit's own form asks for it, and the day
        the rate is from (the last working day before a weekend or a future date). Only the
        two currency codes and the day are sent."""
        try:
            response = self.rates.get(day.isoformat(), params={"base": base, "symbols": target})
        except httpx.HTTPError:
            raise SpliitError("rate_unavailable") from None
        rate = response.json().get("rates", {}).get(target) if response.status_code == 200 else None
        if rate is None:
            raise SpliitError("rate_unavailable", f"No exchange rate from {base} to {target}.")
        return Decimal(str(rate)), response.json()["date"]

    # ---------- Writes ----------

    def create_expense(self, group_id, form, participant_id):
        """participant_id is who Spliit's activity log names as the author."""
        return self.call("POST", "groups.expenses.create", {
            "groupId": group_id, "expenseFormValues": form, "participantId": participant_id})["expenseId"]

    def update_expense(self, group_id, expense_id, form, participant_id):
        self.call("POST", "groups.expenses.update", {
            "groupId": group_id, "expenseId": expense_id, "expenseFormValues": form,
            "participantId": participant_id})

    def delete_expense(self, group_id, expense_id, participant_id):
        """For good: Spliit keeps no copy, only a line in the group's activity."""
        self.call("POST", "groups.expenses.delete", {
            "groupId": group_id, "expenseId": expense_id, "participantId": participant_id})
