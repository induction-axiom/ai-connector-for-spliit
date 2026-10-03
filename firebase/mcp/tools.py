"""The MCP tools: thin wrappers over Spliit, in names and decimal amounts.

The AI names groups and people; it never sees a group ID, since that is the key to the
group. Expected failures come back as results with error_code and next_action, not tool
errors, so the AI learns what to do next. Every write is logged in Firestore with the
expense before and after; update_expense also returns the previous values.
"""
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal
import time
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from spliit import NO_DECIMALS, SpliitError

Amount = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$",
                              description='Decimal string in the group currency, such as "12.50"')]
GroupName = Annotated[str, Field(min_length=1, max_length=100, description="A name from list_groups")]
PersonName = Annotated[str, Field(min_length=1, max_length=100, description="A member's name from list_groups")]
ExpenseId = Annotated[str, Field(min_length=1, max_length=64)]
Title = Annotated[str, Field(min_length=2, max_length=200)]
Notes = Annotated[str, Field(max_length=5000)]
SplitMode = Literal["EVENLY", "BY_SHARES", "BY_PERCENTAGE", "BY_AMOUNT"]

NEXT_ACTIONS = {
    "no_groups": "add_group", "group_missing": "check_group_link",
    "spliit_unavailable": "retry_later", "not_found": "check_ids",
    "request_rejected": "fix_arguments", "invalid_arguments": "fix_arguments",
    "possible_duplicate": "confirm_with_owner",
}
# An expense with the same amount, added this recently, may be the same one.
DUPLICATE_DAYS = 3


class Portion(BaseModel):
    name: PersonName
    share: Annotated[str | None, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$", description=(
        "Ignored for EVENLY. For BY_SHARES a number of shares, for BY_PERCENTAGE a percent, "
        "for BY_AMOUNT an amount in the group currency."))] = None


class Failure(Exception):
    """A tool's expected failure; run() turns it into a result for the AI."""

    def __init__(self, code, detail=None, **extra):
        super().__init__(code)
        self.code, self.detail, self.extra = code, detail, extra


# ---------- Money: Spliit counts in minor units ----------

def places(currency_code):
    return 0 if currency_code in NO_DECIMALS else 2


def to_minor(amount, currency_code):
    return int(Decimal(amount).scaleb(places(currency_code)))


def to_major(minor, currency_code):
    return str(Decimal(minor).scaleb(-places(currency_code)))


def to_shares(mode, share, currency_code):
    """Spliit stores shares and percents times 100, and amounts in minor units."""
    if mode == "EVENLY":
        return 100
    if share is None:
        raise Failure("invalid_arguments", f"each person in paid_for needs a share for {mode}")
    if mode == "BY_AMOUNT":
        return to_minor(share, currency_code)
    return int(Decimal(share) * 100)


def from_shares(mode, shares, currency_code):
    if mode == "EVENLY":
        return None
    if mode == "BY_AMOUNT":
        return to_major(shares, currency_code)
    return str(Decimal(shares) / 100)


# ---------- What the AI sees ----------

def expense_view(expense, group):
    """One expense in names and decimal amounts. Both list and get answers fit here."""
    names = {p["id"]: p["name"] for p in group["participants"]}
    code = group["currencyCode"]
    mode = expense["splitMode"]
    view = {"id": expense["id"], "title": expense["title"], "date": expense["expenseDate"][:10],
            "amount": to_major(expense["amount"], code),
            "category": (expense.get("category") or {}).get("name"),
            "paid_by": names.get(expense["paidBy"]["id"]), "split_mode": mode,
            "paid_for": [{"name": names.get(p.get("participantId") or p["participant"]["id"]),
                          "share": from_shares(mode, p["shares"], code)} for p in expense["paidFor"]],
            "is_reimbursement": expense["isReimbursement"], "created_at": expense["createdAt"]}
    if "notes" in expense:
        view["notes"] = expense["notes"]
    return view


def form_from(expense):
    """Spliit's update replaces the whole expense; start from what it is now."""
    form = {"expenseDate": expense["expenseDate"], "title": expense["title"],
            "category": expense["categoryId"], "amount": expense["amount"],
            "paidBy": expense["paidById"], "splitMode": expense["splitMode"],
            "paidFor": [{"participant": p["participantId"], "shares": p["shares"]} for p in expense["paidFor"]],
            "isReimbursement": expense["isReimbursement"], "saveDefaultSplittingOptions": False,
            "documents": [{k: d[k] for k in ("id", "url", "width", "height")} for d in expense["documents"]],
            "notes": expense["notes"] or "", "recurrenceRule": expense["recurrenceRule"] or "NONE"}
    for name in ("originalAmount", "originalCurrency", "conversionRate"):
        if expense.get(name) is not None:
            form[name] = expense[name]
    return form


def register(server, spliit, groups, changes, who, owner_url, connector, security):
    """Add the tools to server. groups holds the saved groups, changes logs every write,
    and who() names the AI app making this call. Returns the read tools by name, for the
    dashboard's preview."""
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                           openWorldHint=True)
    # Adding twice makes two expenses; changes are logged with their previous values.
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                            openWorldHint=True)

    def run(work):
        try:
            return {"ok": True, **work()}
        except (SpliitError, Failure) as error:
            result = {"ok": False, "error_code": error.code,
                      "next_action": NEXT_ACTIONS.get(error.code, "tell_owner")}
            if isinstance(error, Failure):
                result.update(error.extra)
            if error.detail:
                result["detail"] = error.detail
            if error.code in {"no_groups", "group_missing"}:
                result["owner_url"] = owner_url
            return result

    def load(name):
        """The saved entry and the live group for a group name."""
        saved = groups.get()
        if not saved:
            raise Failure("no_groups", "No Spliit group is added yet.")
        entry = next((g for g in saved if g["name"].casefold() == name.strip().casefold()), None)
        if entry is None:
            raise Failure("invalid_arguments", "Groups: " + ", ".join(g["name"] for g in saved))
        group = spliit.group(entry["id"])
        if group is None:
            raise Failure("group_missing", f"Spliit has no group behind the saved {entry['name']} link.")
        return entry, group

    def person(group, name):
        found = [p["id"] for p in group["participants"] if p["name"].casefold() == name.strip().casefold()]
        if len(found) != 1:
            raise Failure("invalid_arguments", "Members: " + ", ".join(p["name"] for p in group["participants"]))
        return found[0]

    def log(tool, entry, group, expense_id, before):
        after = expense_view(spliit.expense(entry["id"], expense_id), group)
        changes.add_change({"at": time.time(), "app": who(), "tool": tool, "group": entry["name"],
                            "expense_id": expense_id, "before": before, "after": after})
        return after

    def add(tool, entry, group, form, allow_duplicate):
        if not allow_duplicate:
            since = datetime.now(timezone.utc) - timedelta(days=DUPLICATE_DAYS)
            recent = spliit.expenses(entry["id"], 0, 30)["expenses"]
            same = [expense_view(e, group) for e in recent
                    if e["amount"] == form["amount"] and datetime.fromisoformat(e["createdAt"]) >= since]
            if same:
                raise Failure("possible_duplicate", created=False, possible_duplicates=same)
        expense_id = spliit.create_expense(entry["id"], form, entry["me"])
        return {"created": True, "expense": log(tool, entry, group, expense_id, None)}

    # ---------- Read ----------

    @server.tool(annotations=read, meta=security)
    def list_groups() -> dict[str, Any]:
        """List the Spliit groups the owner added, with their currency, members, and which
        member is the owner.

        owner_url is this deployment's own dashboard, where groups are added. Give the
        owner that exact clickable link when they ask where to manage the connector, or
        when next_action is add_group or check_group_link.
        """
        def work():
            listed = []
            for entry in groups.get():
                group = spliit.group(entry["id"])
                if group is None:
                    listed.append({"name": entry["name"], "error_code": "group_missing"})
                    continue
                me = next((p["name"] for p in group["participants"] if p["id"] == entry["me"]), None)
                listed.append({"name": entry["name"], "currency": group["currencyCode"] or group["currency"],
                               "members": [p["name"] for p in group["participants"]], "owner_is": me})
            return {"groups": listed}
        return {**run(work), "owner_url": owner_url, "connector": connector}

    @server.tool(annotations=read, meta=security)
    def get_balances(group: GroupName) -> dict[str, Any]:
        """Each member's balance in a group, and the reimbursements Spliit suggests to settle up.

        A positive balance means the group owes that member; negative means they owe.
        In reimbursements, "from" pays "to" the amount.
        """
        def work():
            entry, live = load(group)
            names = {p["id"]: p["name"] for p in live["participants"]}
            code = live["currencyCode"]
            answer = spliit.balances(entry["id"])
            return {"currency": code or live["currency"],
                    "balances": [{"name": names.get(pid), "paid": to_major(b["paid"], code),
                                  "paid_for": to_major(b["paidFor"], code), "balance": to_major(b["total"], code)}
                                 for pid, b in answer["balances"].items()],
                    "reimbursements": [{"from": names.get(r["from"]), "to": names.get(r["to"]),
                                        "amount": to_major(r["amount"], code)} for r in answer["reimbursements"]]}
        return run(work)

    @server.tool(annotations=read, meta=security)
    def list_expenses(group: GroupName,
                      title_contains: Annotated[str | None, Field(max_length=200)] = None,
                      limit: Annotated[int, Field(ge=1, le=100)] = 50,
                      offset: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        """List a group's expenses, newest first.

        is_reimbursement true marks a settle-up, not spending. For each person in
        paid_for, share means shares, a percent or an amount, as split_mode says; with
        EVENLY it is null. Read every page (next_offset) before totaling.
        """
        def work():
            entry, live = load(group)
            answer = spliit.expenses(entry["id"], offset, limit, title_contains)
            return {"currency": live["currencyCode"] or live["currency"],
                    "expenses": [expense_view(e, live) for e in answer["expenses"]],
                    "next_offset": answer["nextCursor"] if answer["hasMore"] else None}
        return run(work)

    @server.tool(annotations=read, meta=security)
    def get_expense(group: GroupName, expense_id: ExpenseId) -> dict[str, Any]:
        """Read one expense, with its notes."""
        def work():
            entry, live = load(group)
            return {"expense": expense_view(spliit.expense(entry["id"], expense_id), live)}
        return run(work)

    @server.tool(annotations=read, meta=security)
    def list_categories() -> dict[str, Any]:
        """List Spliit's expense categories; use an id as category_id."""
        return run(lambda: {"categories": spliit.categories()})

    # ---------- Write ----------

    @server.tool(annotations=write, meta=security)
    def create_expense(
        group: GroupName,
        title: Title,
        amount: Amount,
        paid_by: PersonName | None = None,
        split_mode: SplitMode = "EVENLY",
        paid_for: Annotated[list[Portion] | None, Field(max_length=100)] = None,
        date: Date | None = None,
        category_id: Annotated[int, Field(ge=0)] = 0,
        notes: Notes | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a new expense in a group, in the group's currency.

        paid_by defaults to the owner; paid_for defaults to every member, split evenly.
        With BY_AMOUNT the shares must add up to amount; with BY_PERCENTAGE, to 100.
        date defaults to today. If error_code is possible_duplicate, nothing was created:
        an expense with the same amount was added in the last 3 days. Show the owner
        possible_duplicates and call again with allow_duplicate true only if they confirm.
        Never repeat a call that may have succeeded; read with list_expenses first.
        """
        def work():
            entry, live = load(group)
            code = live["currencyCode"]
            portions = paid_for or [Portion(name=p["name"]) for p in live["participants"]]
            form = {"expenseDate": (date or Date.today()).isoformat(), "title": title, "category": category_id,
                    "amount": to_minor(amount, code),
                    "paidBy": person(live, paid_by) if paid_by else entry["me"], "splitMode": split_mode,
                    "paidFor": [{"participant": person(live, p.name), "shares": to_shares(split_mode, p.share, code)}
                                for p in portions],
                    "isReimbursement": False, "saveDefaultSplittingOptions": False, "documents": [],
                    "notes": notes or "", "recurrenceRule": "NONE"}
            return add("create_expense", entry, live, form, allow_duplicate)
        return run(work)

    @server.tool(annotations=write, meta=security)
    def record_reimbursement(
        group: GroupName,
        amount: Amount,
        to: PersonName,
        paid_by: PersonName | None = None,
        date: Date | None = None,
        notes: Notes | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a settle-up: paid_by (the owner by default) paid this amount to someone
        outside Spliit. Duplicates are handled as in create_expense."""
        def work():
            entry, live = load(group)
            form = {"expenseDate": (date or Date.today()).isoformat(), "title": "Reimbursement",
                    "category": 1,  # Spliit's Payment category, as its own reimbursement form uses
                    "amount": to_minor(amount, live["currencyCode"]),
                    "paidBy": person(live, paid_by) if paid_by else entry["me"], "splitMode": "EVENLY",
                    "paidFor": [{"participant": person(live, to), "shares": 100}],
                    "isReimbursement": True, "saveDefaultSplittingOptions": False, "documents": [],
                    "notes": notes or "", "recurrenceRule": "NONE"}
            return add("record_reimbursement", entry, live, form, allow_duplicate)
        return run(work)

    @server.tool(annotations=write, meta=security)
    def update_expense(
        group: GroupName,
        expense_id: ExpenseId,
        title: Title | None = None,
        amount: Amount | None = None,
        paid_by: PersonName | None = None,
        split_mode: SplitMode | None = None,
        paid_for: Annotated[list[Portion] | None, Field(max_length=100)] = None,
        date: Date | None = None,
        category_id: Annotated[int | None, Field(ge=0)] = None,
        notes: Notes | None = None,
    ) -> dict[str, Any]:
        """Change an expense; pass only what changes.

        paid_for replaces everyone's share, so give all of them; with BY_AMOUNT a new
        amount needs new shares. previous holds the expense before this change: to undo
        it, call update_expense again with its values.
        """
        def work():
            entry, live = load(group)
            code = live["currencyCode"]
            current = spliit.expense(entry["id"], expense_id)
            form = form_from(current)
            if title is not None:
                form["title"] = title
            if amount is not None:
                form["amount"] = to_minor(amount, code)
            if paid_by is not None:
                form["paidBy"] = person(live, paid_by)
            if split_mode is not None:
                form["splitMode"] = split_mode
            if paid_for is not None:
                form["paidFor"] = [{"participant": person(live, p.name),
                                    "shares": to_shares(form["splitMode"], p.share, code)} for p in paid_for]
            if date is not None:
                form["expenseDate"] = date.isoformat()
            if category_id is not None:
                form["category"] = category_id
            if notes is not None:
                form["notes"] = notes
            before = expense_view(current, live)
            spliit.update_expense(entry["id"], expense_id, form, entry["me"])
            return {"expense": log("update_expense", entry, live, expense_id, before), "previous": before}
        return run(work)

    return {"list_groups": list_groups, "get_balances": get_balances, "list_expenses": list_expenses}
