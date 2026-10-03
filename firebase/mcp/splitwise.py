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
DETAIL_LIMIT = 300


class SplitwiseError(Exception):
    """code is ours and stable; detail is Splitwise's own text, untrusted and shortened."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code, self.detail = code, detail


def trim(value):
    if isinstance(value, dict):
        return {k: trim(v) for k, v in value.items() if k not in DROPPED}
    if isinstance(value, list):
        return [trim(v) for v in value]
    return value


def error_text(errors):
    """Splitwise reports {"base": ["..."], "cost": ["..."]}, a list, or a string."""
    if isinstance(errors, dict):
        parts = [f"{k}: {m}" if k != "base" else str(m)
                 for k, v in errors.items() for m in (v if isinstance(v, list) else [v])]
    elif isinstance(errors, list):
        parts = [str(m) for m in errors]
    else:
        parts = [str(errors)] if errors else []
    return "; ".join(parts)[:DETAIL_LIMIT] or None


def shares_fields(shares):
    fields = {}
    for index, share in enumerate(shares):
        for name in ("user_id", "paid_share", "owed_share"):
            fields[f"users__{index}__{name}"] = share[name]
    return fields


class Splitwise:
    """key has get() -> str | None and invalidate(); observe(code) hears each outcome,
    None for success, so the dashboard can show when Splitwise last answered."""

    def __init__(self, key, transport=None, observe=None, timeout=20.0):
        self.key, self.observe = key, observe or (lambda code: None)
        self.http = httpx.Client(base_url=API, transport=transport, timeout=timeout,
                                 follow_redirects=False,
                                 headers={"Accept": "application/json",
                                          "User-Agent": "ai-connector-for-splitwise"})

    def call(self, method, path, query=None, body=None, key=None):
        try:
            data = self._call(method, path, query, body, key)
        except SplitwiseError as error:
            # A rejected request is about its arguments, not about Splitwise or the key.
            if error.code not in {"request_rejected", "not_found"}:
                self.observe(error.code)
            raise
        self.observe(None)
        return data

    def _call(self, method, path, query, body, key):
        token = key or self.key.get()
        if not token:
            raise SplitwiseError("key_missing")
        query = {k: v for k, v in (query or {}).items() if v is not None}
        for attempt in (1, 2):
            try:
                response = self.http.request(method, path, params=query or None, json=body,
                                             headers={"Authorization": "Bearer " + token})
            except httpx.HTTPError:
                raise SplitwiseError("splitwise_unavailable") from None
            if response.status_code == 401 and key is None and attempt == 1:
                # The owner may have replaced the key from another instance: read it once more.
                self.key.invalidate()
                fresh = self.key.get()
                if fresh and fresh != token:
                    token = fresh
                    continue
            break
        return self.check(response)

    @staticmethod
    def check(response):
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        detail = error_text((data or {}).get("errors") or (data or {}).get("error")) \
            if isinstance(data, dict) else None
        if status == 401:
            raise SplitwiseError("key_rejected", detail)
        if status == 403:
            raise SplitwiseError("forbidden", detail)
        if status == 404:
            raise SplitwiseError("not_found", detail)
        if status == 429:
            raise SplitwiseError("rate_limited", response.headers.get("retry-after"))
        if status >= 500:
            raise SplitwiseError("splitwise_unavailable")
        if not isinstance(data, dict):
            raise SplitwiseError("response_invalid")
        if status >= 400 or error_text(data.get("errors")):
            raise SplitwiseError("request_rejected", detail)
        return data

    @staticmethod
    def field(data, name, kind):
        value = data.get(name)
        if not isinstance(value, kind):
            raise SplitwiseError("response_invalid")
        return value

    # ---------- Reads ----------

    def current_user(self, key=None):
        return self.field(self.call("GET", "get_current_user", key=key), "user", dict)

    def groups(self):
        return self.field(self.call("GET", "get_groups"), "groups", list)

    def friends(self):
        return self.field(self.call("GET", "get_friends"), "friends", list)

    def categories(self):
        return self.field(self.call("GET", "get_categories"), "categories", list)

    def expense(self, expense_id):
        return self.field(self.call("GET", f"get_expense/{int(expense_id)}"), "expense", dict)

    def expenses(self, **query):
        return self.field(self.call("GET", "get_expenses", query=query), "expenses", list)

    def comments(self, expense_id):
        return self.field(self.call("GET", "get_comments", query={"expense_id": int(expense_id)}),
                          "comments", list)

    # ---------- Writes ----------

    def create_expense(self, fields, shares=None, payment=False):
        body = {**fields, **shares_fields(shares or [])}
        if payment:
            body["payment"] = True  # not in the published spec; Splitwise's apps send it
        return self._first_expense(self.call("POST", "create_expense", body=body))

    def update_expense(self, expense_id, fields, shares=None):
        body = {**fields, **shares_fields(shares or [])}
        return self._first_expense(self.call("POST", f"update_expense/{int(expense_id)}", body=body))

    def delete_expense(self, expense_id):
        self._succeeded(self.call("POST", f"delete_expense/{int(expense_id)}"))

    def undelete_expense(self, expense_id):
        self._succeeded(self.call("POST", f"undelete_expense/{int(expense_id)}"))

    def create_comment(self, expense_id, content):
        data = self.call("POST", "create_comment",
                         body={"expense_id": int(expense_id), "content": content})
        return self.field(data, "comment", dict)

    def _first_expense(self, data):
        expenses = self.field(data, "expenses", list)
        if not expenses or not isinstance(expenses[0], dict):
            raise SplitwiseError("response_invalid")
        return expenses[0]

    @staticmethod
    def _succeeded(data):
        if data.get("success") is not True:
            raise SplitwiseError("request_rejected", error_text(data.get("errors")))
