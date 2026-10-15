"""Independent schedule checks and a tiny exhaustive priority oracle."""

import copy
import itertools
import random
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from backend.planner import plan_day


def task(task_id, duration, resources=None, after=None, required=True, priority=1):
    return {"id": task_id, "label": task_id, "duration": duration,
            "resources": resources or [], "after": after or [],
            "required": required, "priority": priority}


def snapshot(tasks, deadline=10, resources=None, now=0, completed=None, blocks=None):
    return {"now": now, "deadline": deadline, "resources": resources or [],
            "tasks": tasks, "completed": completed or [], "blocks": blocks or []}


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def assert_valid_schedule(case, source, result):
    """Check raw constraints without using planner validation/model helpers."""
    case.assertIn(result["status"], ("OPTIMAL", "FEASIBLE"))
    case.assertEqual((result["now"], result["deadline"]), (source["now"], source["deadline"]))
    original = {entry["id"]: entry for entry in source["tasks"]}
    scheduled = {entry["id"]: entry for entry in result["scheduled"]}
    case.assertEqual(len(scheduled), len(result["scheduled"]))
    completed = set(source["completed"])
    case.assertTrue(set(scheduled) <= set(original) - completed)
    case.assertEqual(set(result["deferred"]), set(original) - completed - set(scheduled))
    case.assertEqual(len(result["deferred"]), len(set(result["deferred"])))
    case.assertEqual(result["conflicts"], [])
    for task_id, raw in original.items():
        if raw["required"] and task_id not in completed:
            case.assertIn(task_id, scheduled)
        if task_id not in scheduled:
            continue
        entry = scheduled[task_id]
        case.assertEqual(entry["label"], raw["label"])
        case.assertEqual(entry["resources"], raw["resources"])
        case.assertEqual(entry["end"] - entry["start"], raw["duration"])
        case.assertGreaterEqual(entry["start"], source["now"])
        case.assertLessEqual(entry["end"], source["deadline"])
        for parent in raw["after"]:
            if parent not in completed:
                case.assertIn(parent, scheduled)
                case.assertLessEqual(scheduled[parent]["end"], entry["start"])
        for block in source["blocks"]:
            if block["resource"] in entry["resources"]:
                case.assertFalse(overlaps(entry["start"], entry["end"], block["start"], block["end"]))
    for first, second in itertools.combinations(result["scheduled"], 2):
        if set(first["resources"]) & set(second["resources"]):
            case.assertFalse(overlaps(first["start"], first["end"], second["start"], second["end"]))
    objective = result["objective"]
    case.assertEqual(objective["priority_score"], sum(original[key]["priority"] for key in scheduled))
    case.assertEqual(objective["finish"], max([entry["end"] for entry in scheduled.values()] + [source["now"]]))
    case.assertEqual(objective["start_total"], sum(entry["start"] - source["now"] for entry in scheduled.values()))


def brute_force_priority(source):
    """Enumerate subsets and integer start times; return None when impossible."""
    completed = set(source["completed"])
    remaining = [entry for entry in source["tasks"] if entry["id"] not in completed]
    best = None
    for bits in itertools.product((False, True), repeat=len(remaining)):
        chosen = [entry for entry, present in zip(remaining, bits) if present]
        ids = {entry["id"] for entry in chosen}
        if any(entry["required"] and entry["id"] not in ids for entry in remaining):
            continue
        if any(not set(entry["after"]) <= ids | completed for entry in chosen):
            continue
        score = sum(entry["priority"] for entry in chosen)
        if best is not None and score <= best:
            continue
        windows = [range(source["now"], source["deadline"] - entry["duration"] + 1) for entry in chosen]
        for starts in itertools.product(*windows):
            times = {entry["id"]: (start, start + entry["duration"]) for entry, start in zip(chosen, starts)}
            if any(times[parent][1] > times[entry["id"]][0]
                   for entry in chosen for parent in entry["after"] if parent not in completed):
                continue
            if any(overlaps(*times[entry["id"]], block["start"], block["end"])
                   for entry in chosen for block in source["blocks"] if block["resource"] in entry["resources"]):
                continue
            if any(set(first["resources"]) & set(second["resources"])
                   and overlaps(*times[first["id"]], *times[second["id"]])
                   for first, second in itertools.combinations(chosen, 2)):
                continue
            best = score
            break
    return best


class PlannerTests(unittest.TestCase):
    def solve_valid(self, source):
        before = copy.deepcopy(source)
        result = plan_day(source)
        self.assertEqual(source, before, "planning must not mutate its input")
        assert_valid_schedule(self, source, result)
        return result

    def test_parallel_people_and_shared_bathroom_printer(self):
        source = snapshot([
            task("alex-shower", 3, ["bathroom", "alex"]),
            task("sam-shower", 3, ["bathroom", "sam"]),
            task("alex-print", 2, ["printer", "alex"], ["alex-shower"]),
            task("sam-print", 2, ["printer", "sam"], ["sam-shower"]),
        ], 8, ["bathroom", "printer", "alex", "sam"])
        result = self.solve_valid(source)
        self.assertEqual(result["objective"]["finish"], 8)
        self.assertEqual(result["deferred"], [])

    def test_independent_people_can_start_together(self):
        result = self.solve_valid(snapshot([task("alex", 4, ["alex"]), task("sam", 4, ["sam"])],
                                           4, ["alex", "sam"]))
        self.assertEqual([entry["start"] for entry in result["scheduled"]], [0, 0])

    def test_required_shortage_returns_only_sufficient_required_core(self):
        source = snapshot([task("a", 3, ["bathroom"]), task("b", 3, ["bathroom"]),
                           task("optional", 1, ["bathroom"], required=False)], 5, ["bathroom"])
        result = plan_day(source)
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(set(result["conflicts"]), {"a", "b"})
        self.assertEqual(result["scheduled"], [])
        self.assertEqual(result["deferred"], [])
        self.assertIsNone(result["objective"])
        core_only = copy.deepcopy(source)
        for entry in core_only["tasks"]:
            entry["required"] = entry["id"] in result["conflicts"]
        self.assertEqual(plan_day(core_only)["status"], "INFEASIBLE")

    def test_impossible_deadline(self):
        result = plan_day(snapshot([task("too-long", 6)], 5))
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["conflicts"], ["too-long"])

    def test_completed_dependency_consumes_no_remaining_time(self):
        source = snapshot([task("done", 180, ["bathroom"]), task("next", 2, ["bathroom"], ["done"])],
                          12, ["bathroom"], now=10, completed=["done"])
        result = self.solve_valid(source)
        self.assertEqual([entry["id"] for entry in result["scheduled"]], ["next"])
        self.assertEqual(result["scheduled"][0]["start"], 10)

    def test_required_child_forces_optional_parent(self):
        source = snapshot([task("parent", 2, required=False), task("child", 2, after=["parent"])], 4)
        self.assertEqual(len(self.solve_valid(source)["scheduled"]), 2)
        source["deadline"] = 3
        result = plan_day(source)
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["conflicts"], ["child"])

    def test_optional_child_cannot_skip_parent(self):
        source = snapshot([task("parent", 3, ["r"], required=False),
                           task("child", 1, ["r"], ["parent"], required=False, priority=10),
                           task("other", 3, ["r"], required=False, priority=5)], 3, ["r"])
        result = self.solve_valid(source)
        self.assertEqual([entry["id"] for entry in result["scheduled"]], ["other"])
        self.assertEqual(set(result["deferred"]), {"parent", "child"})

    def test_user_priority_beats_every_earlier_finish_preference(self):
        source = snapshot([task("important", 180, ["r"], required=False, priority=10),
                           task("quick", 1, ["r"], required=False, priority=9)], 180, ["r"])
        result = self.solve_valid(source)
        self.assertEqual([entry["id"] for entry in result["scheduled"]], ["important"])
        self.assertEqual(result["objective"]["priority_score"], 10)

    def test_priority_sum_then_finish_then_starts(self):
        source = snapshot([task("long", 6, ["r"], required=False, priority=10),
                           task("short-a", 3, ["r"], required=False, priority=6),
                           task("short-b", 3, ["r"], required=False, priority=6)], 6, ["r"])
        self.assertEqual(self.solve_valid(source)["objective"]["priority_score"], 12)
        source = snapshot([task("long", 6, ["r"], required=False, priority=5),
                           task("short", 1, ["r"], required=False, priority=5)], 6, ["r"])
        self.assertEqual([entry["id"] for entry in self.solve_valid(source)["scheduled"]], ["short"])
        source = snapshot([task("fixed", 5, ["a"]), task("movable", 1, ["b"])], 5, ["a", "b"])
        self.assertEqual(self.solve_valid(source)["objective"]["start_total"], 0)

    def test_overlapping_touching_and_duplicate_blocks_are_one_unavailability(self):
        blocks = [{"resource": "r", "start": start, "end": end}
                  for start, end in [(-5, 2), (0, 2), (1, 3), (3, 4), (3, 4), (9, 12)]]
        source = snapshot([task("a", 2, ["r"])], 6, ["r"], blocks=blocks)
        result = self.solve_valid(source)
        self.assertEqual((result["scheduled"][0]["start"], result["scheduled"][0]["end"]), (4, 6))

    def test_interruption_changes_resource_availability(self):
        source = snapshot([task("a", 2, ["r"])], 5, ["r"])
        self.assertEqual(self.solve_valid(source)["scheduled"][0]["start"], 0)
        source["blocks"] = [{"resource": "r", "start": 0, "end": 3}]
        self.assertEqual(self.solve_valid(source)["scheduled"][0]["start"], 3)
        source["blocks"][0]["end"] = 4
        self.assertEqual(plan_day(source)["status"], "INFEASIBLE")

    def test_readyby_demo_interruption_and_explicit_quick_wash(self):
        source = snapshot([
            task("alex_bathroom", 6, ["Alex", "Bathroom"]),
            task("sam_shower", 8, ["Sam", "Bathroom"]),
            task("lunch", 6, ["Sam"]),
            task("pack", 3, ["Alex", "Sam"], ["alex_bathroom", "sam_shower", "lunch"]),
            task("shoes", 2, ["Alex", "Sam"], ["pack"]),
        ], 60, ["Alex", "Sam", "Bathroom"], now=40)
        self.assertEqual(self.solve_valid(source)["objective"]["finish"], 59)
        source["now"] = 48
        source["completed"] = ["alex_bathroom", "lunch"]
        source["blocks"] = [{"resource": "Bathroom", "start": 48, "end": 52}]
        self.assertEqual(plan_day(source)["status"], "INFEASIBLE")
        source["deadline"] = 65
        self.assertEqual(self.solve_valid(source)["objective"]["finish"], 65)
        source["deadline"] = 60
        source["tasks"][1].update(duration=3, label="Quick wash")
        result = self.solve_valid(source)
        self.assertEqual(result["objective"]["finish"], 60)
        self.assertEqual(next(entry["label"] for entry in result["scheduled"]
                              if entry["id"] == "sam_shower"), "Quick wash")

    def test_empty_and_all_completed(self):
        result = self.solve_valid(snapshot([], 1))
        self.assertEqual(result["objective"], {"priority_score": 0, "finish": 0, "start_total": 0})
        result = self.solve_valid(snapshot([task("done", 3)], 11, now=10, completed=["done"]))
        self.assertEqual(result["objective"]["finish"], 10)

    def test_small_random_cases_match_exhaustive_oracle(self):
        rng = random.Random(391)
        for index in range(35):
            entries = []
            for number in range(4):
                resources = [resource for resource in ("a", "b") if rng.random() < 0.45]
                after = [str(number - 1)] if number and rng.random() < 0.3 else []
                entries.append(task(str(number), rng.randint(1, 3), resources, after,
                                    required=rng.random() < 0.4, priority=rng.randint(1, 10)))
            blocks = [{"resource": "a", "start": 1, "end": 2}] if index % 2 else []
            source = snapshot(entries, 5, ["a", "b"], blocks=blocks,
                              completed=["0"] if index % 5 == 0 else [])
            expected = brute_force_priority(source)
            with self.subTest(case=index, expected=expected):
                result = plan_day(source)
                if expected is None:
                    self.assertEqual(result["status"], "INFEASIBLE")
                else:
                    assert_valid_schedule(self, source, result)
                    self.assertEqual(result["status"], "OPTIMAL")
                    self.assertEqual(result["objective"]["priority_score"], expected)

    def test_strict_primitive_types_and_bounds(self):
        valid = snapshot([task("a", 2, ["r"])], 5, ["r"])
        mutations = [
            lambda s: s.update(now=True), lambda s: s.update(deadline=False),
            lambda s: s.update(now=0.0), lambda s: s.update(deadline="5"),
            lambda s: s.update(deadline=0), lambda s: s.update(deadline=181),
            lambda s: s.update(resources=("r",)), lambda s: s.update(tasks={}),
            lambda s: s.update(completed="a"), lambda s: s.update(blocks={}),
            lambda s: s["tasks"][0].update(duration=True),
            lambda s: s["tasks"][0].update(duration=1.5),
            lambda s: s["tasks"][0].update(duration=0),
            lambda s: s["tasks"][0].update(duration=181),
            lambda s: s["tasks"][0].update(priority=True),
            lambda s: s["tasks"][0].update(priority=0),
            lambda s: s["tasks"][0].update(priority=11),
            lambda s: s["tasks"][0].update(required=1),
            lambda s: s["tasks"][0].update(resources="r"),
            lambda s: s["tasks"][0].update(after=("a",)),
            lambda s: s["tasks"][0].update(id=1),
            lambda s: s["tasks"][0].update(label=" "),
            lambda s: s.update(resources=[str(n) for n in range(9)]),
            lambda s: s.update(tasks=[task(str(n), 1) for n in range(13)]),
            lambda s: s.update(blocks=[{"resource": "r", "start": True, "end": 2}]),
            lambda s: s.update(blocks=[{"resource": "r", "start": 1, "end": 1}]),
            lambda s: s.update(blocks=[{"resource": [], "start": 0, "end": 1}]),
            lambda s: s.update(extra=True),
        ]
        for index, mutation in enumerate(mutations):
            invalid = copy.deepcopy(valid)
            mutation(invalid)
            with self.subTest(case=index), self.assertRaises(ValueError):
                plan_day(invalid)
        for value in (None, [], True):
            with self.assertRaises(ValueError):
                plan_day(value)

    def test_duplicates_unknown_references_and_cycles(self):
        cases = [
            snapshot([task("a", 1), task("a", 1)]),
            snapshot([task("a", 1)], resources=["r", "r"]),
            snapshot([task("a", 1, ["r", "r"])], resources=["r"]),
            snapshot([task("a", 1)], completed=["a", "a"]),
            snapshot([task("a", 1, after=["missing"])]),
            snapshot([task("a", 1)], completed=["missing"]),
            snapshot([task("a", 1, ["missing"])]),
            snapshot([task("a", 1)], blocks=[{"resource": "missing", "start": 0, "end": 1}]),
            snapshot([task("a", 1, after=["a"])]),
            snapshot([task("a", 1, after=["b"]), task("b", 1, after=["a"])], completed=["a"]),
            snapshot([task("a", 1), task("b", 1, after=["a", "a"])]),
        ]
        for index, invalid in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                plan_day(invalid)

    def test_unknown_makes_no_infeasibility_or_optimality_claim(self):
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.UNKNOWN):
            result = plan_day(snapshot([task("a", 1)], 1))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["scheduled"], [])
        self.assertEqual(result["deferred"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertIsNone(result["objective"])
        self.assertIn("undetermined", result["quality"])

    def test_feasible_does_not_certify_priority_optimum(self):
        real_solve = cp_model.CpSolver.solve

        def feasible(solver, model):
            self.assertEqual(real_solve(solver, model), cp_model.OPTIMAL)
            return cp_model.FEASIBLE

        source = snapshot([task("a", 1)], 1)
        with patch.object(cp_model.CpSolver, "solve", feasible):
            result = plan_day(source)
        assert_valid_schedule(self, source, result)
        self.assertEqual(result["status"], "FEASIBLE")
        self.assertIn("not certified", result["quality"])


if __name__ == "__main__":
    unittest.main()
