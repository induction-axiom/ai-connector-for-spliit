"""The OAuth record store from ai_connector_core, plus the log of changes the AI made
in Spliit."""
from copy import deepcopy

from ai_connector_core import store

# The tools that add an expense, as the change log names them.
ADDING = {"create_expense", "record_reimbursement"}


class MemoryStore(store.MemoryStore):
    """Synthetic tests only; production always uses Firestore."""
    def __init__(self):
        super().__init__()
        self.changes = []

    def add_change(self, record):
        with self.lock:
            self.changes.append(deepcopy(record))

    def added_by_ai(self, expense_id):
        with self.lock:
            return any(r["expense_id"] == expense_id and r["tool"] in ADDING for r in self.changes)

    def changes_to(self, expense_ids):
        with self.lock:
            return deepcopy([r for r in self.changes if r["expense_id"] in set(expense_ids)])

    def recent_changes(self, count, before=None):
        with self.lock:
            older = [r for r in self.changes if before is None or r["at"] < before]
            return deepcopy(sorted(older, key=lambda r: r["at"], reverse=True)[:count])


class FirestoreStore(store.FirestoreStore):
    # AI changes to Spliit, kept for good: the dashboard lists them, newest first.
    def add_change(self, record):
        self.db.collection("ai_changes").add(record, timeout=10)

    def added_by_ai(self, expense_id):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.db.collection("ai_changes").where(filter=FieldFilter("expense_id", "==", expense_id))
        return any(doc.get("tool") in ADDING for doc in query.stream(timeout=10))

    def changes_to(self, expense_ids):
        from google.cloud.firestore_v1.base_query import FieldFilter
        ids = sorted(set(expense_ids))
        found = []
        for start in range(0, len(ids), 30):  # Firestore's limit for "in"
            query = self.db.collection("ai_changes").where(filter=FieldFilter("expense_id", "in", ids[start:start + 30]))
            found += [doc.to_dict() for doc in query.stream(timeout=10)]
        return found

    def recent_changes(self, count, before=None):
        from google.cloud import firestore
        query = self.db.collection("ai_changes").order_by("at", direction=firestore.Query.DESCENDING)
        if before is not None:
            query = query.start_after({"at": before})
        query = query.limit(count)
        return [doc.to_dict() for doc in query.stream(timeout=10)]
