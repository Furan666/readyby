"""Persistent fictional household; MCP may draft, only the review UI commits."""
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.planner import plan_day


def example():
    return {
        "now": 40, "deadline": 60, "resources": ["Alex", "Sam", "Bathroom"],
        "tasks": [
            {"id": "alex_bathroom", "label": "Get ready", "duration": 6, "resources": ["Alex", "Bathroom"], "after": [], "required": True, "priority": 5},
            {"id": "sam_shower", "label": "Shower", "duration": 8, "resources": ["Sam", "Bathroom"], "after": [], "required": True, "priority": 5},
            {"id": "lunch", "label": "Pack lunches", "duration": 6, "resources": ["Sam"], "after": [], "required": True, "priority": 5},
            {"id": "pack", "label": "Pack together", "duration": 3, "resources": ["Alex", "Sam"], "after": ["alex_bathroom", "sam_shower", "lunch"], "required": True, "priority": 5},
            {"id": "shoes", "label": "Shoes & leave", "duration": 2, "resources": ["Alex", "Sam"], "after": ["pack"], "required": True, "priority": 5}
        ], "completed": [], "blocks": []
    }


def describe(snapshot, revision):
    """Find earliest mandatory-feasible deadline, without calling a timeout impossible."""
    plan = plan_day(snapshot)
    earliest, earliest_deadline = None, None
    if plan["status"] == "INFEASIBLE":
        low, high = snapshot["deadline"] + 1, snapshot["now"] + 180
        candidate = copy.deepcopy(snapshot)
        candidate["deadline"] = high
        upper = plan_day(candidate)
        if upper["status"] in ("OPTIMAL", "FEASIBLE"):
            earliest, earliest_deadline = upper, high
            while low <= high:
                middle = (low + high) // 2
                candidate["deadline"] = middle
                trial = plan_day(candidate)
                if trial["status"] in ("OPTIMAL", "FEASIBLE"):
                    earliest, earliest_deadline = trial, middle
                    high = middle - 1
                elif trial["status"] == "INFEASIBLE":
                    low = middle + 1
                else:
                    # No lower-bound/earliest claim if a time-limited solve is unknown.
                    earliest, earliest_deadline = None, None
                    break
    return {"revision": revision, "snapshot": snapshot, "plan": plan,
            "earliest_plan": earliest, "earliest_deadline": earliest_deadline}


class StaleDraft(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS household (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, snapshot TEXT NOT NULL, summary TEXT NOT NULL, changes TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO household VALUES (1,1,?)", (json.dumps(example()),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self):
        with self.connect() as db:
            revision, snapshot = db.execute("SELECT revision,snapshot FROM household WHERE id=1").fetchone()
        return json.loads(snapshot), revision

    def view(self):
        snapshot, revision = self.read()
        return describe(snapshot, revision)

    def draft(self, changes):
        if type(changes) is not dict or not changes or set(changes) - {"now", "deadline", "completed", "blocks", "task_changes"}:
            raise ValueError("Supply only now, deadline, completed, blocks or task_changes.")
        snapshot, revision = self.read()
        original = copy.deepcopy(snapshot)
        summaries = []
        for field in ("now", "deadline", "completed", "blocks"):
            if field in changes:
                snapshot[field] = copy.deepcopy(changes[field])
                summaries.append({"field": field, "before": original[field], "after": changes[field]})
        if "task_changes" in changes:
            patches = changes["task_changes"]
            if type(patches) is not list or not 1 <= len(patches) <= 12:
                raise ValueError("task_changes must be a list of 1 to 12 edits.")
            by_id = {task["id"]: task for task in snapshot["tasks"]}
            seen = set()
            for patch in patches:
                if type(patch) is not dict or set(patch) - {"id", "duration", "label"} or "id" not in patch or len(patch) < 2:
                    raise ValueError("Each task edit needs an id and duration or label.")
                task_id = patch["id"]
                if type(task_id) is not str or task_id not in by_id or task_id in seen or task_id in snapshot["completed"]:
                    raise ValueError("Edit only distinct known unfinished tasks.")
                seen.add(task_id)
                task = by_id[task_id]
                for field in ("duration", "label"):
                    if field in patch:
                        summaries.append({"field": f"{task_id}.{field}", "before": task[field], "after": patch[field]})
                        task[field] = patch[field]
        # Validate before comparisons to avoid coercion and before creating any draft.
        plan_day(snapshot)
        if snapshot["now"] < original["now"] or not set(original["completed"]) <= set(snapshot["completed"]):
            raise ValueError("Time and completed work cannot move backwards; reset the fictional example instead.")
        result = describe(snapshot, revision)
        draft_id = uuid.uuid4().hex
        with self.connect() as db:
            # Old previews are disposable; cap retained records per local household.
            db.execute("DELETE FROM drafts WHERE rowid NOT IN (SELECT rowid FROM drafts ORDER BY rowid DESC LIMIT 19)")
            db.execute("INSERT INTO drafts VALUES (?,?,?,?,?)", (draft_id, revision, json.dumps(snapshot), json.dumps(summaries), json.dumps(changes)))
        return {**result, "id": draft_id, "base_revision": revision, "summary": summaries, "changes": changes}

    def commit(self, draft_id, base_revision):
        if type(draft_id) is not str or type(base_revision) is not int:
            raise ValueError("A draft id and integer revision are required.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM household WHERE id=1").fetchone()[0]
            row = db.execute("SELECT revision,snapshot FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None or revision != base_revision or row[0] != revision:
                raise StaleDraft("This preview is stale or already applied. Refresh and review again.")
            db.execute("UPDATE household SET revision=?, snapshot=? WHERE id=1", (revision + 1, row[1]))
            db.execute("DELETE FROM drafts")
        return self.view()

    def reset(self):
        with self.connect() as db:
            db.execute("UPDATE household SET revision=revision+1,snapshot=? WHERE id=1", (json.dumps(example()),))
            db.execute("DELETE FROM drafts")
        return self.view()

    def recovery_choices(self):
        view = self.view()
        choices = []
        if view["plan"]["status"] in ("OPTIMAL", "FEASIBLE"):
            return {"choices": choices, "reason": "The current mandatory tasks fit. No recovery is needed."}
        if view["plan"]["status"] != "INFEASIBLE":
            return {"choices": choices, "reason": "The solver did not establish feasibility. No impossibility or recovery claim is available."}
        if view["earliest_deadline"] is not None:
            choices.append({**self.draft({"deadline": view["earliest_deadline"]}), "title": "Keep shower, leave later"})
        shower = next((task for task in view["snapshot"]["tasks"] if task["id"] == "sam_shower"), None)
        if shower and shower["id"] not in view["snapshot"]["completed"] and shower["duration"] > 3:
            # A fictional household supplied this acceptable alternative; the solver did not invent it.
            choices.append({**self.draft({"task_changes": [{"id": "sam_shower", "duration": 3, "label": "Quick wash"}]}), "title": "Use a 3-minute wash"})
        return {"choices": choices, "reason": "These are household-provided alternatives. Neither is applied until reviewed."}
