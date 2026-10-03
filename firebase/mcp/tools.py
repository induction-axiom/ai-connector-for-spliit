"""The MCP tools: thin wrappers over Splitwise, with checks before every write.

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
Id = Annotated[int, Field(ge=1, le=2**53)]
When = Annotated[str, Field(max_length=40, description="ISO 8601 date or date-time")]
Description = Annotated[str, Field(min_length=1, max_length=255)]
Details = Annotated[str, Field(max_length=2000, description='Notes ("details" in Splitwise)')]

NEXT_ACTIONS = {
    "key_missing": "add_splitwise_key", "key_rejected": "replace_splitwise_key",
    "rate_limited": "retry_later", "splitwise_unavailable": "retry_later",
    "response_invalid": "retry_later", "forbidden": "check_ids", "not_found": "check_ids",
    "request_rejected": "fix_arguments", "invalid_arguments": "fix_arguments",
    "possible_duplicate": "confirm_with_owner", "payment_not_supported": "tell_owner",
}
# A recent expense with the same amount is a likely retry or a double entry.
DUPLICATE_RECENT = timedelta(minutes=15)
DUPLICATE_WINDOW = timedelta(days=2)


class Share(BaseModel):
    user_id: Id
    paid_share: Amount
    owed_share: Amount


class Invalid(Exception):
    pass


def parse_time(value, name):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise Invalid(name + " must be an ISO 8601 date or date-time") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def check_shares(shares, cost):
    ids = [share.user_id for share in shares]
    if len(shares) < 2 or len(set(ids)) != len(ids):
        raise Invalid("shares needs at least two different users")
    for field in ("paid_share", "owed_share"):
        total = sum(Decimal(getattr(share, field)) for share in shares)
        if total != Decimal(cost):
            raise Invalid(f"{field} values add up to {total}, not the cost {cost}")
    return [share.model_dump() for share in shares]


def expense_summary(expense):
    return {k: expense.get(k) for k in ("id", "description", "cost", "currency_code", "date",
                                         "created_at", "group_id", "payment")}


def previous_values(expense):
    """What update_expense needs to put an expense back as it was."""
    return {"description": expense.get("description"), "cost": expense.get("cost"),
            "currency_code": expense.get("currency_code"), "date": expense.get("date"),
            "category_id": (expense.get("category") or {}).get("id"),
            "details": expense.get("details"), "group_id": expense.get("group_id"),
            "shares": [{"user_id": u.get("user_id"), "paid_share": u.get("paid_share"),
                        "owed_share": u.get("owed_share")} for u in expense.get("users") or []]}


def register(server, splitwise, owner_url, connector, security, clock=lambda: datetime.now(timezone.utc)):
    """Add the tools to server; returns them by name for the dashboard's preview."""
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                           openWorldHint=True)
    # Creating twice makes two expenses; nothing here destroys data that can't be restored.
    create = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                             openWorldHint=True)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                             openWorldHint=True)

    def failure(code, detail=None, **extra):
        result = {"ok": False, "error_code": code, "next_action": NEXT_ACTIONS.get(code, "tell_owner"),
                  **extra}
        if detail:
            result["detail"] = detail
        if code in {"key_missing", "key_rejected"}:
            result["owner_url"] = owner_url
        return result

    def run(work):
        try:
            result = work()
            return result if "ok" in result else {"ok": True, **result}
        except SplitwiseError as error:
            return failure(error.code, error.detail)
        except Invalid as error:
            return failure("invalid_arguments", str(error))

    def duplicates(cost, currency_code, description, date, group_id):
        """Live expenses near the same date with the same amount and either the same
        description or created in the last few minutes."""
        moment = parse_time(date, "date") if date else clock()
        rows = splitwise.expenses(dated_after=(moment - DUPLICATE_WINDOW).isoformat(),
                                  dated_before=(moment + DUPLICATE_WINDOW).isoformat(),
                                  group_id=group_id or None, limit=100)
        now, found = clock(), []
        for row in rows:
            try:
                same_cost = Decimal(row.get("cost") or "x") == Decimal(cost)
                created = parse_time(row.get("created_at"), "created_at")
            except (ArithmeticError, Invalid):
                continue
            if (row.get("deleted_at") or not same_cost
                    or (currency_code and row.get("currency_code") != currency_code)):
                continue
            if ((row.get("description") or "").strip().casefold() == description.strip().casefold()
                    or now - created <= DUPLICATE_RECENT):
                found.append(expense_summary(row))
        return found

    def create_checked(fields, shares, allow_duplicate, payment=False):
        if not allow_duplicate:
            found = duplicates(fields["cost"], fields.get("currency_code"), fields["description"],
                               fields.get("date"), fields.get("group_id"))
            if found:
                return failure("possible_duplicate", None, created=False, possible_duplicates=found)
        expense = splitwise.create_expense(fields, shares, payment=payment)
        return {"created": True, "expense": trim(expense)}

    def optional(**values):
        return {k: v for k, v in values.items() if v is not None}

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
        group_id: Annotated[int | None, Field(ge=0)] = None,
        friend_id: Id | None = None,
        dated_after: When | None = None,
        dated_before: When | None = None,
        updated_after: When | None = None,
        updated_before: When | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0, le=100000)] = 0,
    ) -> dict[str, Any]:
        """List expenses, newest first, optionally for one group (0 = no group) or friend.

        Rows with deleted_at set were deleted; skip them when totaling. payment true marks
        a settle-up, not spending. Each row's users list shows paid_share, owed_share and
        net_balance per person; the owner's spending is their owed_share. Total per
        currency, never converted. Read every page (next_offset) before totaling.
        """
        def work():
            for name, value in (("dated_after", dated_after), ("dated_before", dated_before),
                                ("updated_after", updated_after), ("updated_before", updated_before)):
                if value is not None:
                    parse_time(value, name)
            rows = splitwise.expenses(group_id=group_id, friend_id=friend_id,
                                      dated_after=dated_after, dated_before=dated_before,
                                      updated_after=updated_after, updated_before=updated_before,
                                      limit=limit, offset=offset)
            return {"expenses": trim(rows),
                    "next_offset": offset + len(rows) if len(rows) >= limit else None}
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
        group_id: Annotated[int | None, Field(ge=0)] = None,
        split_equally: bool = False,
        shares: Annotated[list[Share] | None, Field(max_length=50)] = None,
        date: When | None = None,
        category_id: Id | None = None,
        details: Details | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a new expense in Splitwise.

        Either set split_equally with a group_id (the owner paid, split among all group
        members), or give shares: one per person, with what they paid and what they owe.
        paid_share and owed_share each must add up to cost. Without currency_code,
        Splitwise uses the owner's default currency; without date, now.
        If error_code is possible_duplicate, nothing was created: show the owner
        possible_duplicates and call again with allow_duplicate true only if they confirm.
        Never repeat a call that may have succeeded; read with list_expenses first.
        """
        def work():
            if split_equally == (shares is not None):
                raise Invalid("give either split_equally with a group_id, or shares")
            if split_equally and not group_id:
                raise Invalid("split_equally needs a group_id")
            if Decimal(cost) <= 0:
                raise Invalid("cost must be more than zero")
            fields = optional(description=description, cost=cost, currency_code=currency_code,
                              group_id=group_id, date=date, category_id=category_id, details=details)
            if date:
                parse_time(date, "date")
            if split_equally:
                fields["split_equally"] = True
            checked = check_shares(shares, cost) if shares is not None else None
            return create_checked(fields, checked, allow_duplicate)
        return run(work)

    @server.tool(annotations=create, meta=security)
    def record_payment(
        to_user_id: Id,
        amount: Amount,
        from_user_id: Id | None = None,
        currency_code: Currency | None = None,
        group_id: Annotated[int | None, Field(ge=0)] = None,
        date: When | None = None,
        details: Details | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a settle-up: from_user_id paid to_user_id this amount outside Splitwise.

        from_user_id defaults to the owner. It shows in Splitwise as a payment and reduces
        what from_user_id owes to_user_id. Duplicates are handled as in create_expense.
        """
        def work():
            if Decimal(amount) <= 0:
                raise Invalid("amount must be more than zero")
            payer = from_user_id or splitwise.current_user().get("id")
            if payer == to_user_id:
                raise Invalid("from_user_id and to_user_id must differ")
            fields = optional(description="Payment", cost=amount, currency_code=currency_code,
                              group_id=group_id, date=date, details=details)
            if date:
                parse_time(date, "date")
            shares = [{"user_id": payer, "paid_share": amount, "owed_share": "0.00"},
                      {"user_id": to_user_id, "paid_share": "0.00", "owed_share": amount}]
            result = create_checked(fields, shares, allow_duplicate, payment=True)
            expense = result.get("expense")
            if expense and expense.get("payment") is not True:
                # Splitwise ignored payment and saved a regular expense: take it back out.
                splitwise.delete_expense(expense["id"])
                return failure("payment_not_supported",
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
        group_id: Annotated[int | None, Field(ge=0)] = None,
        shares: Annotated[list[Share] | None, Field(max_length=50)] = None,
        date: When | None = None,
        category_id: Id | None = None,
        details: Details | None = None,
    ) -> dict[str, Any]:
        """Change an expense; pass only what changes.

        shares replaces every share, so give all of them; a new cost needs new shares.
        previous holds the values before this change: to undo it, call update_expense
        again with them.
        """
        def work():
            fields = optional(description=description, cost=cost, currency_code=currency_code,
                              group_id=group_id, date=date, category_id=category_id, details=details)
            if not fields and shares is None:
                raise Invalid("nothing to change")
            if cost is not None and shares is None:
                raise Invalid("a new cost needs new shares that add up to it")
            if date:
                parse_time(date, "date")
            before = splitwise.expense(expense_id)
            if before.get("deleted_at"):
                raise Invalid("this expense is deleted; restore_expense it first")
            checked = check_shares(shares, cost or before.get("cost")) if shares is not None else None
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
