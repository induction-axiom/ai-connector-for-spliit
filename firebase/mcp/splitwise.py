"""Splitwise Self-Serve API v3.0, and the one place that knows its quirks.

Requests use only documented parameters (tests check them against the published spec),
except `payment` on create_expense, which marks a settle-up. A 200 answer succeeds only
when its `errors` is empty. Amounts are decimal strings; shares go out as flat
users__{index}__{property} fields.
"""
import httpx

API = "https://secure.splitwise.com/api/v3.0/"
# Pictures and icons are noise to an AI, and an invite link lets anyone join that group.
DROPPED = {"picture", "custom_picture", "avatar", "custom_avatar", "cover_photo", "invite_link",
           "receipt", "icon", "icon_types"}
STATUS_CODES = {401: "key_rejected", 403: "forbidden", 404: "not_found", 429: "rate_limited"}


class SplitwiseError(Exception):
    """code is ours and stable; detail is Splitwise's own text, untrusted."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail


def trim(value):
    if isinstance(value, dict):
        return {k: trim(v) for k, v in value.items() if k not in DROPPED}
    if isinstance(value, list):
        return [trim(v) for v in value]
    return value


def shares_fields(shares):
    fields = {}
    for index, share in enumerate(shares or []):
        for name in ("user_id", "paid_share", "owed_share"):
            fields[f"users__{index}__{name}"] = share[name]
    return fields


class Splitwise:
    """key has get() -> the saved API key or None. on_failure(code) hears each failure
    that is about that key or Splitwise itself, so the dashboard can show the last one."""

    def __init__(self, key, on_failure, transport=None):
        self.key, self.on_failure = key, on_failure
        self.http = httpx.Client(base_url=API, transport=transport, timeout=20)

    def call(self, method, path, query=None, body=None, key=None):
        try:
            return self._call(method, path, query, body, key or self.key.get())
        except SplitwiseError as error:
            # Only failures with the saved key count; checking a pasted key doesn't, and a
            # rejected request is about its arguments, not about Splitwise or the key.
            if key is None and error.code not in {"request_rejected", "not_found"}:
                self.on_failure(error.code)
            raise

    def _call(self, method, path, query, body, key):
        if not key:
            raise SplitwiseError("key_missing")
        query = {k: v for k, v in (query or {}).items() if v is not None}
        try:
            response = self.http.request(method, path, params=query, json=body,
                                         headers={"Authorization": "Bearer " + key})
        except httpx.HTTPError:
            raise SplitwiseError("splitwise_unavailable") from None
        if response.status_code >= 500:
            raise SplitwiseError("splitwise_unavailable")
        if response.status_code in STATUS_CODES:
            raise SplitwiseError(STATUS_CODES[response.status_code])
        data = response.json()
        errors = "; ".join(str(m) for messages in (data.get("errors") or {}).values() for m in messages)
        if response.status_code >= 400 or errors:
            raise SplitwiseError("request_rejected", errors or None)
        return data

    # ---------- Reads ----------

    def current_user(self, key=None):
        return self.call("GET", "get_current_user", key=key)["user"]

    def groups(self):
        return self.call("GET", "get_groups")["groups"]

    def friends(self):
        return self.call("GET", "get_friends")["friends"]

    def categories(self):
        return self.call("GET", "get_categories")["categories"]

    def expense(self, expense_id):
        return self.call("GET", f"get_expense/{expense_id}")["expense"]

    def expenses(self, **query):
        return self.call("GET", "get_expenses", query=query)["expenses"]

    def comments(self, expense_id):
        return self.call("GET", "get_comments", query={"expense_id": expense_id})["comments"]

    # ---------- Writes ----------

    def create_expense(self, fields, shares, payment=False):
        body = {**fields, **shares_fields(shares)}
        if payment:
            body["payment"] = True  # not in the published spec; Splitwise's apps send it
        return self.call("POST", "create_expense", body=body)["expenses"][0]

    def update_expense(self, expense_id, fields, shares):
        body = {**fields, **shares_fields(shares)}
        return self.call("POST", f"update_expense/{expense_id}", body=body)["expenses"][0]

    def delete_expense(self, expense_id):
        if not self.call("POST", f"delete_expense/{expense_id}")["success"]:
            raise SplitwiseError("request_rejected")

    def undelete_expense(self, expense_id):
        if not self.call("POST", f"undelete_expense/{expense_id}")["success"]:
            raise SplitwiseError("request_rejected")

    def create_comment(self, expense_id, content):
        return self.call("POST", "create_comment",
                         body={"expense_id": expense_id, "content": content})["comment"]
