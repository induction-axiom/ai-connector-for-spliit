"""The MCP tools: thin wrappers over Splitwise.

Expected failures come back as results with error_code and next_action, not tool errors,
so the AI learns what to do next. Every write can be undone: delete with
restore_expense, and update_expense returns the previous values.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from splitwise import SplitwiseError, trim

Amount = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$",
                              description='Decimal string with at most 2 places, such as "12.50"')]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3,5}$", description="Splitwise currency code, such as CAD")]
Id = Annotated[int, Field(ge=1)]
GroupId = Annotated[int, Field(ge=0, description="0 means no group")]
Description = Annotated[str, Field(min_length=1, max_length=255)]
Details = Annotated[str, Field(max_length=2000, description='Notes ("details" in Splitwise)')]

NEXT_ACTIONS = {
    "key_missing": "add_splitwise_key", "key_rejected": "replace_splitwise_key",
    "rate_limited": "retry_later", "splitwise_unavailable": "retry_later",
    "forbidden": "check_ids", "not_found": "check_ids",
    "request_rejected": "fix_arguments", "invalid_arguments": "fix_arguments",
    "possible_duplicate": "confirm_with_owner", "payment_not_supported": "tell_owner",
}
# An expense with the same cost created or changed this recently may be the same one.
DUPLICATE_DAYS = 3


class Share(BaseModel):
    user_id: Id
    paid_share: Amount
    owed_share: Amount


Shares = Annotated[list[Share], Field(max_length=50)]


class Failure(Exception):
    """A tool's expected failure; run() turns it into a result for the AI."""

    def __init__(self, code, detail=None, **extra):
        super().__init__(code)
        self.code, self.detail, self.extra = code, detail, extra


def check_shares(shares, cost):
    for field in ("paid_share", "owed_share"):
        total = sum(Decimal(getattr(share, field)) for share in shares)
        if total != Decimal(cost):
            raise Failure("invalid_arguments", f"{field} values add up to {total}, not the cost {cost}")
    return [share.model_dump() for share in shares]


def iso(moment):
    return moment.isoformat() if moment else None


def optional(**values):
    return {k: v for k, v in values.items() if v is not None}


def previous_values(expense):
    """What update_expense needs to put an expense back as it was."""
    return {"description": expense["description"], "cost": expense["cost"],
            "currency_code": expense["currency_code"], "date": expense["date"],
            "category_id": expense["category"]["id"], "details": expense["details"],
            "group_id": expense["group_id"],
            "shares": [{"user_id": u["user_id"], "paid_share": u["paid_share"],
                        "owed_share": u["owed_share"]} for u in expense["users"]]}


def register(server, splitwise, owner_url, connector, security):
    """Add the tools to server; returns the read tools by name for the dashboard's preview."""
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                           openWorldHint=True)
    # Creating twice makes two expenses; nothing here destroys data that can't be restored.
    create = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                             openWorldHint=True)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                             openWorldHint=True)

    def run(work):
        try:
            return {"ok": True, **work()}
        except (SplitwiseError, Failure) as error:
            result = {"ok": False, "error_code": error.code,
                      "next_action": NEXT_ACTIONS.get(error.code, "tell_owner")}
            if isinstance(error, Failure):
                result.update(error.extra)
            if error.detail:
                result["detail"] = error.detail
            if error.code in {"key_missing", "key_rejected"}:
                result["owner_url"] = owner_url
            return result

    def add_expense(fields, shares, allow_duplicate, payment=False):
        if not allow_duplicate:
            since = datetime.now(timezone.utc) - timedelta(days=DUPLICATE_DAYS)
            recent = splitwise.expenses(updated_after=since.isoformat(), limit=100)
            same = [{k: e[k] for k in ("id", "description", "cost", "currency_code", "date", "created_at")}
                    for e in recent
                    if not e["deleted_at"] and Decimal(e["cost"]) == Decimal(fields["cost"])]
            if same:
                raise Failure("possible_duplicate", created=False, possible_duplicates=same)
        return {"created": True, "expense": trim(splitwise.create_expense(fields, shares, payment))}

    # ---------- Read ----------

    @server.tool(annotations=read, meta=security)
    def get_status() -> dict[str, Any]:
        """Check that Splitwise answers with the owner's key, and who the owner is there.

        owner_url is this deployment's own dashboard. Give the owner that exact clickable
        link when they ask where to manage the connector, or when next_action is
        add_splitwise_key or replace_splitwise_key.
        """
        result = run(lambda: {"user": trim(splitwise.current_user())})
        return {**result, "owner_url": owner_url, "connector": connector}

    @server.tool(annotations=read, meta=security)
    def list_groups() -> dict[str, Any]:
        """List the owner's groups with members, balances and debts.

        Group id 0 holds expenses outside any group. In original_debts and
        simplified_debts, user "from" owes user "to" the amount. Balances are per currency;
        never add different currencies together.
        """
        return run(lambda: {"groups": trim(splitwise.groups())})

    @server.tool(annotations=read, meta=security)
    def list_friends() -> dict[str, Any]:
        """List the owner's friends with balances per currency, overall and per group.

        A positive amount means that friend owes the owner; negative means the owner owes
        them. Use the ids here as user_id in shares and payments.
        """
        return run(lambda: {"friends": trim(splitwise.friends())})

    @server.tool(annotations=read, meta=security)
    def list_expenses(
        group_id: GroupId | None = None,
        friend_id: Id | None = None,
        dated_after: datetime | None = None,
        dated_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List expenses, newest first, optionally for one group or friend.

        Rows with deleted_at set were deleted; skip them when totaling. payment true marks
        a settle-up, not spending. Each row's users list shows paid_share, owed_share and
        net_balance per person; the owner's spending is their owed_share. Total per
        currency, never converted. Read every page (next_offset) before totaling.
        """
        def work():
            rows = splitwise.expenses(group_id=group_id, friend_id=friend_id,
                                      dated_after=iso(dated_after), dated_before=iso(dated_before),
                                      updated_after=iso(updated_after), updated_before=iso(updated_before),
                                      limit=limit, offset=offset)
            return {"expenses": trim(rows), "next_offset": offset + limit if len(rows) == limit else None}
        return run(work)

    @server.tool(annotations=read, meta=security)
    def get_expense(expense_id: Id) -> dict[str, Any]:
        """Read one expense with its shares and comments."""
        return run(lambda: {"expense": trim(splitwise.expense(expense_id)),
                            "comments": trim(splitwise.comments(expense_id))})

    @server.tool(annotations=read, meta=security)
    def list_categories() -> dict[str, Any]:
        """List Splitwise's expense categories and subcategories; use a subcategory id as category_id."""
        return run(lambda: {"categories": trim(splitwise.categories())})

    # ---------- Write ----------

    @server.tool(annotations=create, meta=security)
    def create_expense(
        description: Description,
        cost: Amount,
        currency_code: Currency | None = None,
        group_id: GroupId | None = None,
        split_equally: bool = False,
        shares: Shares | None = None,
        date: datetime | None = None,
        category_id: Id | None = None,
        details: Details | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a new expense in Splitwise.

        Either set split_equally with a group_id (the owner paid, split among all group
        members), or give shares: one per person, with what they paid and what they owe.
        paid_share and owed_share each must add up to cost. Without currency_code,
        Splitwise uses the owner's default currency; without date, now.
        If error_code is possible_duplicate, nothing was created: an expense with the same
        cost was added or changed in the last 3 days. Show the owner possible_duplicates
        and call again with allow_duplicate true only if they confirm.
        Never repeat a call that may have succeeded; read with list_expenses first.
        """
        def work():
            if split_equally == (shares is not None):
                raise Failure("invalid_arguments", "give either split_equally with a group_id, or shares")
            fields = optional(description=description, cost=cost, currency_code=currency_code,
                              group_id=group_id, date=iso(date), category_id=category_id,
                              details=details, split_equally=split_equally or None)
            checked = check_shares(shares, cost) if shares else None
            return add_expense(fields, checked, allow_duplicate)
        return run(work)

    @server.tool(annotations=create, meta=security)
    def record_payment(
        to_user_id: Id,
        amount: Amount,
        from_user_id: Id | None = None,
        currency_code: Currency | None = None,
        group_id: GroupId | None = None,
        date: datetime | None = None,
        details: Details | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a settle-up: from_user_id paid to_user_id this amount outside Splitwise.

        from_user_id defaults to the owner. It shows in Splitwise as a payment and reduces
        what from_user_id owes to_user_id. Duplicates are handled as in create_expense.
        """
        def work():
            payer = from_user_id or splitwise.current_user()["id"]
            fields = optional(description="Payment", cost=amount, currency_code=currency_code,
                              group_id=group_id, date=iso(date), details=details)
            shares = [{"user_id": payer, "paid_share": amount, "owed_share": "0.00"},
                      {"user_id": to_user_id, "paid_share": "0.00", "owed_share": amount}]
            result = add_expense(fields, shares, allow_duplicate, payment=True)
            expense = result["expense"]
            if not expense["payment"]:
                # Splitwise ignored payment and saved a regular expense: take it back out.
                splitwise.delete_expense(expense["id"])
                raise Failure("payment_not_supported",
                              "Splitwise saved it as a regular expense, so it was deleted again.",
                              deleted_expense_id=expense["id"])
            return result
        return run(work)

    @server.tool(annotations=change, meta=security)
    def update_expense(
        expense_id: Id,
        description: Description | None = None,
        cost: Amount | None = None,
        currency_code: Currency | None = None,
        group_id: GroupId | None = None,
        shares: Shares | None = None,
        date: datetime | None = None,
        category_id: Id | None = None,
        details: Details | None = None,
    ) -> dict[str, Any]:
        """Change an expense; pass only what changes.

        shares replaces every share, so give all of them; a new cost needs new shares.
        previous holds the values before this change: to undo it, call update_expense
        again with them.
        """
        def work():
            before = splitwise.expense(expense_id)
            fields = optional(description=description, cost=cost, currency_code=currency_code,
                              group_id=group_id, date=iso(date), category_id=category_id, details=details)
            checked = check_shares(shares, cost or before["cost"]) if shares else None
            after = splitwise.update_expense(expense_id, fields, checked)
            return {"expense": trim(after), "previous": previous_values(before)}
        return run(work)

    @server.tool(annotations=change, meta=security)
    def delete_expense(expense_id: Id) -> dict[str, Any]:
        """Delete an expense. Splitwise keeps it, so restore_expense brings it back."""
        def work():
            splitwise.delete_expense(expense_id)
            return {"deleted": True, "expense_id": expense_id}
        return run(work)

    @server.tool(annotations=change, meta=security)
    def restore_expense(expense_id: Id) -> dict[str, Any]:
        """Bring back a deleted expense."""
        def work():
            splitwise.undelete_expense(expense_id)
            return {"restored": True, "expense": trim(splitwise.expense(expense_id))}
        return run(work)

    @server.tool(annotations=create, meta=security)
    def add_comment(expense_id: Id,
                    content: Annotated[str, Field(min_length=1, max_length=2000)]) -> dict[str, Any]:
        """Add a comment to an expense. Everyone on the expense can see it."""
        return run(lambda: {"comment": trim(splitwise.create_comment(expense_id, content))})

    return {"get_status": get_status, "list_groups": list_groups, "list_friends": list_friends,
            "list_expenses": list_expenses}
