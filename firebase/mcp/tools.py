"""The MCP tools: thin wrappers over Spliit, in names and decimal amounts.

The AI names groups and people; it never sees a group ID, since that is the key to the
group. Expected failures come back as results with error_code and next_action, not tool
errors, so the AI learns what to do next. Every write is logged in Firestore with the
expense before and after; update_expense and delete_expense also return the previous values.
"""
from datetime import date as Date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
import time
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from groups import sync_names
from spliit import NO_DECIMALS, SpliitError

Amount = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$",
                              description='Decimal string in the group currency, such as "12.50"')]
OriginalAmount = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$", description=(
    'What was paid in original_currency, such as "3000", when it differs from the group currency'))]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", description='ISO 4217 code, such as "JPY"')]
Rate = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,12})?$", description=(
    "How much 1 original_currency is in the group currency. Leave it out to use the day's rate."))]
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
    "possible_duplicate": "confirm_with_owner", "rate_unavailable": "ask_owner_for_amount",
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
    minor = Decimal(amount).scaleb(places(currency_code))
    if minor != minor.to_integral_value():
        raise Failure("invalid_arguments", f"{currency_code} amounts have no decimals")
    return int(minor)


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
    if expense.get("originalAmount") is not None:
        currency = expense["originalCurrency"]
        view["original"] = {"amount": to_major(expense["originalAmount"], currency), "currency": currency}
        if expense.get("conversionRate") is not None:  # only when reading one expense
            view["original"]["rate"] = str(expense["conversionRate"])
    if "notes" in expense:
        view["notes"] = expense["notes"]
    return view


def converted(group, day, amount, original_amount, original_currency, rate, rates):
    """The amount in minor units of the group currency, Spliit's conversion fields, and the
    day's exchange rate if it was looked up with rates (Spliit.exchange_rate). Spliit counts
    the group-currency amount; the original amount and rate are kept beside it, for reference."""
    code = group["currencyCode"]
    if original_amount is None and original_currency is None:
        if amount is None:
            raise Failure("invalid_arguments", "Give amount, or original_amount and original_currency.")
        return to_minor(amount, code), {}, None
    if original_amount is None or original_currency is None:
        raise Failure("invalid_arguments", "Give original_amount and original_currency together.")
    if original_currency == code:
        return to_minor(amount or original_amount, code), {}, None
    if not code:
        raise Failure("invalid_arguments", "This group has no currency code in Spliit, so amounts "
                      "can't be converted. Give amount in the group's currency.")
    looked_up = None
    if rate is not None:
        rate = Decimal(rate)
    elif amount is not None:
        with localcontext() as context:
            context.prec = 6
            rate = Decimal(amount) / Decimal(original_amount)
    else:
        rate, rate_date = rates(day, original_currency, code)
        looked_up = {"rate": str(rate), "rate_date": rate_date, "source": "European Central Bank, via frankfurter.dev"}
    if amount is None:
        amount = (Decimal(original_amount) * rate).quantize(Decimal(1).scaleb(-places(code)), ROUND_HALF_UP)
    fields = {"originalAmount": to_minor(original_amount, original_currency),
              "originalCurrency": original_currency, "conversionRate": float(rate)}
    return to_minor(amount, code), fields, looked_up


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
    # Spliit deletes for good; the change log keeps the expense, to add it again.
    delete = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
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
        find = lambda saved: next((g for g in saved if g["name"].casefold() == name.strip().casefold()), None)
        entry = find(saved)
        if entry is None:
            # The owner may use a name the group was given in Spliit since.
            saved = sync_names(groups, saved, {g["id"]: spliit.group(g["id"]) for g in saved})
            entry = find(saved)
        if entry is None:
            raise Failure("invalid_arguments", "Groups: " + ", ".join(g["name"] for g in saved))
        group = spliit.group(entry["id"])
        if group is None:
            raise Failure("group_missing", f"Spliit has no group behind the saved {entry['name']} link.")
        if group["name"] != entry["name"]:
            entry = next(g for g in sync_names(groups, saved, {entry["id"]: group}) if g["id"] == entry["id"])
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

    def add(tool, entry, group, form, allow_duplicate, rate=None):
        if not allow_duplicate:
            since = datetime.now(timezone.utc) - timedelta(days=DUPLICATE_DAYS)
            recent = spliit.expenses(entry["id"], 0, 30)["expenses"]
            same = [expense_view(e, group) for e in recent
                    if e["amount"] == form["amount"] and datetime.fromisoformat(e["createdAt"]) >= since]
            if same:
                raise Failure("possible_duplicate", created=False, possible_duplicates=same)
        expense_id = spliit.create_expense(entry["id"], form, entry["me"])
        result = {"created": True, "expense": log(tool, entry, group, expense_id, None)}
        return {**result, "exchange_rate": rate} if rate else result

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
            saved = groups.get()
            live = {entry["id"]: spliit.group(entry["id"]) for entry in saved}
            listed = []
            for entry in sync_names(groups, saved, live):
                group = live[entry["id"]]
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

        A positive balance means the group owes that member; negative means they owe;
        zero means they are settled. In reimbursements, "from" pays "to" the amount.
        """
        def work():
            entry, live = load(group)
            names = {p["id"]: p["name"] for p in live["participants"]}
            code = live["currencyCode"]
            answer = spliit.balances(entry["id"])
            # Spliit derives these from its suggested reimbursements, so its paid and paidFor
            # are not what anyone spent, and a settled member is missing.
            totals = {pid: b["total"] for pid, b in answer["balances"].items()}
            return {"currency": code or live["currency"],
                    "balances": [{"name": p["name"], "balance": to_major(totals.get(p["id"], 0), code)}
                                 for p in live["participants"]],
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
        amount: Amount | None = None,
        paid_by: PersonName | None = None,
        split_mode: SplitMode = "EVENLY",
        paid_for: Annotated[list[Portion] | None, Field(max_length=100)] = None,
        date: Date | None = None,
        category_id: Annotated[int, Field(ge=0)] = 0,
        notes: Notes | None = None,
        original_amount: OriginalAmount | None = None,
        original_currency: Currency | None = None,
        conversion_rate: Rate | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a new expense in a group.

        amount is in the group's currency. When the owner paid in another currency, give
        original_amount and original_currency instead: it is converted at that date's rate,
        as Spliit itself does, and exchange_rate says which rate. If the owner knows what it
        came to in the group currency (from a card statement, say), give amount as well,
        and the rate follows from the two. Balances count only the group-currency amount.
        paid_by defaults to the owner; paid_for defaults to every member, split evenly.
        With BY_AMOUNT the shares are in the group currency and must add up to amount;
        with BY_PERCENTAGE, to 100. date defaults to today. If error_code is possible_duplicate, nothing was created:
        an expense with the same amount was added in the last 3 days. Show the owner
        possible_duplicates and call again with allow_duplicate true only if they confirm.
        Never repeat a call that may have succeeded; read with list_expenses first.
        """
        def work():
            entry, live = load(group)
            code = live["currencyCode"]
            day = date or Date.today()
            minor, conversion, rate = converted(live, day, amount, original_amount, original_currency,
                                                conversion_rate, spliit.exchange_rate)
            portions = paid_for or [Portion(name=p["name"]) for p in live["participants"]]
            form = {"expenseDate": day.isoformat(), "title": title, "category": category_id, "amount": minor,
                    "paidBy": person(live, paid_by) if paid_by else entry["me"], "splitMode": split_mode,
                    "paidFor": [{"participant": person(live, p.name), "shares": to_shares(split_mode, p.share, code)}
                                for p in portions],
                    "isReimbursement": False, "saveDefaultSplittingOptions": False, "documents": [],
                    "notes": notes or "", "recurrenceRule": "NONE", **conversion}
            return add("create_expense", entry, live, form, allow_duplicate, rate)
        return run(work)

    @server.tool(annotations=write, meta=security)
    def record_reimbursement(
        group: GroupName,
        to: PersonName,
        amount: Amount | None = None,
        paid_by: PersonName | None = None,
        date: Date | None = None,
        notes: Notes | None = None,
        original_amount: OriginalAmount | None = None,
        original_currency: Currency | None = None,
        conversion_rate: Rate | None = None,
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Record a settle-up: paid_by (the owner by default) paid this amount to someone
        outside Spliit. Amounts, other currencies and duplicates are handled as in
        create_expense."""
        def work():
            entry, live = load(group)
            day = date or Date.today()
            minor, conversion, rate = converted(live, day, amount, original_amount, original_currency,
                                                conversion_rate, spliit.exchange_rate)
            form = {"expenseDate": day.isoformat(), "title": "Reimbursement",
                    "category": 1,  # Spliit's Payment category, as its own reimbursement form uses
                    "amount": minor,
                    "paidBy": person(live, paid_by) if paid_by else entry["me"], "splitMode": "EVENLY",
                    "paidFor": [{"participant": person(live, to), "shares": 100}],
                    "isReimbursement": True, "saveDefaultSplittingOptions": False, "documents": [],
                    "notes": notes or "", "recurrenceRule": "NONE", **conversion}
            return add("record_reimbursement", entry, live, form, allow_duplicate, rate)
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
        original_amount: OriginalAmount | None = None,
        original_currency: Currency | None = None,
        conversion_rate: Rate | None = None,
    ) -> dict[str, Any]:
        """Change an expense; pass only what changes.

        Amounts in another currency work as in create_expense. On an expense with an
        original amount, a new amount keeps the original and changes the rate, and a new
        original_amount keeps the rate. Spliit can't remove the other currency from an
        expense. paid_for replaces everyone's share, so give all of them;
        with BY_AMOUNT a new amount needs new shares. previous holds the expense before
        this change: to undo it, call update_expense again with its values, giving
        previous.original as original_amount, original_currency and conversion_rate.
        """
        def work():
            entry, live = load(group)
            code = live["currencyCode"]
            current = spliit.expense(entry["id"], expense_id)
            form = form_from(current)
            looked_up = None
            if title is not None:
                form["title"] = title
            if original_currency is not None and original_currency == code:
                # Spliit's update leaves out what isn't sent, and refuses null for these.
                if current.get("originalAmount") is not None:
                    raise Failure("invalid_arguments", "Spliit can't remove the other currency from an "
                                  "expense. Change amount instead; the original amount stays for reference.")
                if amount is not None:
                    form["amount"] = to_minor(amount, code)
            elif any(v is not None for v in (amount, original_amount, original_currency, conversion_rate)):
                # What isn't given comes from the expense's current conversion, if it has one.
                had = current.get("originalAmount") is not None
                kept = had and original_currency in (None, current["originalCurrency"])
                currency = original_currency or (current["originalCurrency"] if had else None)
                original = original_amount or (to_major(current["originalAmount"], currency) if kept else None)
                rate = conversion_rate or (str(current["conversionRate"]) if kept and amount is None
                                           and current.get("conversionRate") is not None else None)
                day = date or Date.fromisoformat(current["expenseDate"][:10])
                form["amount"], conversion, looked_up = converted(live, day, amount, original, currency,
                                                                  rate, spliit.exchange_rate)
                form.update(conversion)
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
            result = {"expense": log("update_expense", entry, live, expense_id, before), "previous": before}
            return {**result, "exchange_rate": looked_up} if looked_up else result
        return run(work)

    @server.tool(annotations=delete, meta=security)
    def delete_expense(group: GroupName, expense_id: ExpenseId) -> dict[str, Any]:
        """Delete an expense that the owner's AI apps added, only when the owner asks to
        delete that one.

        Spliit deletes for good. previous holds the expense: to undo, add it again with
        create_expense, or record_reimbursement if is_reimbursement. Expenses added in
        Spliit itself can't be deleted here; the owner deletes them in Spliit.
        """
        def work():
            entry, live = load(group)
            before = expense_view(spliit.expense(entry["id"], expense_id), live)
            if not changes.added_by_ai(expense_id):
                raise Failure("not_added_by_ai", "Only expenses the owner's AI apps added can be deleted here; the owner can delete this one in Spliit.")
            spliit.delete_expense(entry["id"], expense_id, entry["me"])
            changes.add_change({"at": time.time(), "app": who(), "tool": "delete_expense", "group": entry["name"],
                                "expense_id": expense_id, "before": before, "after": None})
            return {"deleted": True, "previous": before}
        return run(work)

    return {"list_groups": list_groups, "get_balances": get_balances, "list_expenses": list_expenses}
