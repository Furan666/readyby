"""Store integration: previews, exact revisions, persistence, and demo recovery."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.store import Store, StaleDraft, describe, example


INTERRUPTION = {
    "now": 48,
    "completed": ["alex_bathroom", "lunch"],
    "blocks": [{"resource": "Bathroom", "start": 48, "end": 52}],
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="readyby-store-test-")
        self.path = Path(self.directory.name) / "household.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def apply_interruption(self):
        preview = self.store.draft(copy.deepcopy(INTERRUPTION))
        return self.store.commit(preview["id"], preview["base_revision"])

    def test_demo_preview_does_not_commit_and_derives_earliest_65(self):
        before = self.store.read()
        initial = self.store.view()
        self.assertEqual(initial["revision"], 1)
        self.assertEqual(initial["plan"]["status"], "OPTIMAL")
        self.assertEqual(initial["plan"]["objective"]["finish"], 59)
        preview = self.store.draft(copy.deepcopy(INTERRUPTION))
        self.assertEqual(self.store.read(), before)
        self.assertEqual(preview["base_revision"], 1)
        self.assertEqual(preview["plan"]["status"], "INFEASIBLE")
        self.assertEqual(preview["earliest_deadline"], 65)
        self.assertEqual(preview["earliest_plan"]["objective"]["finish"], 65)
        self.assertEqual(preview["snapshot"]["deadline"], 60)
        self.assertEqual(preview["earliest_plan"]["deadline"], 65)
        self.assertEqual({entry["field"] for entry in preview["summary"]}, {"now", "completed", "blocks"})
        current = self.store.commit(preview["id"], 1)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["snapshot"], preview["snapshot"])
        self.assertEqual(current["earliest_deadline"], 65)

    def test_recovery_choices_are_unapplied_until_exact_choice_is_committed(self):
        self.apply_interruption()
        before = self.store.read()
        choices = self.store.recovery_choices()["choices"]
        self.assertEqual(len(choices), 2)
        self.assertEqual(self.store.read(), before)
        later = next(choice for choice in choices if choice["changes"] == {"deadline": 65})
        wash = next(choice for choice in choices if "task_changes" in choice["changes"])
        self.assertEqual(later["snapshot"]["deadline"], 65)
        self.assertEqual(later["plan"]["objective"]["finish"], 65)
        self.assertEqual(wash["snapshot"]["deadline"], 60)
        self.assertEqual(wash["plan"]["objective"]["finish"], 60)
        chosen = self.store.commit(wash["id"], wash["base_revision"])
        self.assertEqual(chosen["revision"], 3)
        shower = next(entry for entry in chosen["snapshot"]["tasks"] if entry["id"] == "sam_shower")
        self.assertEqual((shower["duration"], shower["label"]), (3, "Quick wash"))
        self.assertEqual(chosen["snapshot"]["deadline"], 60)
        self.assertEqual(chosen["plan"]["objective"]["finish"], 60)
        with self.assertRaises(StaleDraft):
            self.store.commit(later["id"], later["base_revision"])

    def test_choose_later_keeps_original_shower(self):
        self.apply_interruption()
        choice = next(choice for choice in self.store.recovery_choices()["choices"]
                      if choice["changes"] == {"deadline": 65})
        view = self.store.commit(choice["id"], choice["base_revision"])
        shower = next(entry for entry in view["snapshot"]["tasks"] if entry["id"] == "sam_shower")
        self.assertEqual((shower["duration"], shower["label"]), (8, "Shower"))
        self.assertEqual(view["snapshot"]["deadline"], 65)

    def test_exact_revision_and_stale_replay_leave_active_state_unchanged(self):
        first = self.store.draft({"deadline": 61})
        sibling = self.store.draft({"deadline": 62})
        original = self.store.read()
        for draft_id, revision in ((first["id"], 0), (first["id"], 2), ("missing", 1)):
            with self.subTest(draft_id=draft_id, revision=revision), self.assertRaises(StaleDraft):
                self.store.commit(draft_id, revision)
            self.assertEqual(self.store.read(), original)
        for draft_id, revision in ((first["id"], True), (first["id"], 1.0), (True, 1), (None, 1)):
            with self.subTest(draft_id=draft_id, revision=revision), self.assertRaises(ValueError):
                self.store.commit(draft_id, revision)
            self.assertEqual(self.store.read(), original)
        accepted = self.store.commit(first["id"], first["base_revision"])
        self.assertEqual(accepted["revision"], 2)
        self.assertEqual(accepted["snapshot"]["deadline"], 61)
        for old in (first, sibling):
            with self.assertRaises(StaleDraft):
                self.store.commit(old["id"], old["base_revision"])
        self.assertEqual(self.store.read(), (accepted["snapshot"], 2))

    def test_persistence_across_store_instances_including_uncommitted_preview(self):
        preview = self.store.draft(copy.deepcopy(INTERRUPTION))
        second = Store(self.path)
        self.assertEqual(second.read(), (example(), 1))
        committed = second.commit(preview["id"], preview["base_revision"])
        third = Store(self.path)
        self.assertEqual(third.read(), (committed["snapshot"], 2))
        self.assertEqual(self.store.read(), third.read())
        self.assertEqual(third.view()["earliest_deadline"], 65)

    def test_reset_increments_revision_restores_example_and_invalidates_drafts(self):
        self.apply_interruption()
        preview = self.store.draft({"deadline": 65})
        reset = self.store.reset()
        self.assertEqual(reset["revision"], 3)
        self.assertEqual(reset["snapshot"], example())
        self.assertEqual(reset["plan"]["status"], "OPTIMAL")
        with self.assertRaises(StaleDraft):
            self.store.commit(preview["id"], preview["base_revision"])
        self.assertEqual(Store(self.path).read(), (example(), 3))

    def test_unknown_keys_strict_types_and_unknown_ids_are_rejected_without_state_change(self):
        invalid = [
            None, [], {}, {"unknown": 1}, {"now": 41, "unknown": 1},
            {"now": True}, {"deadline": False}, {"deadline": 60.0},
            {"completed": True}, {"completed": ["missing"]},
            {"completed": ["lunch", "lunch"]}, {"blocks": True},
            {"blocks": [{"resource": "Bathroom", "start": True, "end": 52}]},
            {"blocks": [{"resource": "missing", "start": 48, "end": 52}]},
            {"task_changes": []}, {"task_changes": {}},
            {"task_changes": [{"id": "sam_shower"}]},
            {"task_changes": [{"id": "sam_shower", "required": False}]},
            {"task_changes": [{"id": "sam_shower", "duration": True}]},
            {"task_changes": [{"id": "sam_shower", "duration": 0}]},
            {"task_changes": [{"id": "missing", "duration": 3}]},
            {"task_changes": [{"id": [], "duration": 3}]},
            {"task_changes": [{"id": "sam_shower", "duration": 3}, {"id": "sam_shower", "label": "Wash"}]},
        ]
        before = self.store.read()
        for index, change in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.store.draft(change)
            self.assertEqual(self.store.read(), before)
        with self.store.connect() as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM drafts").fetchone()[0], 0)

    def test_time_and_completed_work_cannot_go_backwards(self):
        preview = self.store.draft({"now": 46, "completed": ["alex_bathroom"]})
        self.store.commit(preview["id"], preview["base_revision"])
        before = self.store.read()
        for change in ({"now": 45}, {"completed": []}, {"now": 60}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.draft(change)
            self.assertEqual(self.store.read(), before)
        next_preview = self.store.draft({"now": 47, "completed": ["alex_bathroom", "lunch"]})
        self.assertEqual(next_preview["snapshot"]["now"], 47)
        self.assertEqual(self.store.read(), before)

    def test_label_is_strictly_typed_and_successful_edit_remains_a_preview(self):
        before = self.store.read()
        for label in (True, 4, None, "", "  ", [], {}):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.store.draft({"task_changes": [{"id": "sam_shower", "label": label}]})
            self.assertEqual(self.store.read(), before)
        preview = self.store.draft({"task_changes": [{"id": "sam_shower", "label": "Quick wash", "duration": 3}]})
        self.assertEqual(self.store.read(), before)
        shower = next(entry for entry in preview["snapshot"]["tasks"] if entry["id"] == "sam_shower")
        self.assertEqual((shower["label"], shower["duration"]), ("Quick wash", 3))
        self.assertEqual({entry["field"] for entry in preview["summary"]}, {"sam_shower.label", "sam_shower.duration"})

    def test_previously_completed_task_cannot_be_edited(self):
        preview = self.store.draft({"completed": ["alex_bathroom"]})
        self.store.commit(preview["id"], preview["base_revision"])
        before = self.store.read()
        for edit in ({"id": "alex_bathroom", "duration": 1}, {"id": "alex_bathroom", "label": "Changed"}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.store.draft({"task_changes": [edit]})
            self.assertEqual(self.store.read(), before)

    def test_task_completed_in_same_update_cannot_be_rewritten(self):
        before = self.store.read()
        with self.assertRaises(ValueError):
            self.store.draft({"completed": ["alex_bathroom"],
                              "task_changes": [{"id": "alex_bathroom", "duration": 1}]})
        self.assertEqual(self.store.read(), before)

    def test_recovery_choices_does_not_claim_unknown_is_feasible(self):
        with patch.object(self.store, "view", return_value={"plan": {"status": "UNKNOWN"}}):
            result = self.store.recovery_choices()
        self.assertEqual(result["choices"], [])
        self.assertNotIn("current mandatory tasks fit", result["reason"].lower())
        self.assertRegex(result["reason"].lower(), r"unknown|undetermined|could not|not.*(?:establish|prov|certif)")

    def test_earliest_search_abandons_claim_after_unknown(self):
        source = example()
        with patch("backend.store.plan_day", side_effect=[
            {"status": "INFEASIBLE"}, {"status": "FEASIBLE"}, {"status": "UNKNOWN"}
        ]) as solve:
            result = describe(source, 7)
        self.assertEqual(result["plan"]["status"], "INFEASIBLE")
        self.assertEqual(solve.call_count, 3)
        self.assertIsNone(result["earliest_deadline"])
        self.assertIsNone(result["earliest_plan"])


if __name__ == "__main__":
    unittest.main()
