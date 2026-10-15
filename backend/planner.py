"""A bounded, deterministic CP-SAT planner. Times are integer minute offsets."""

from ortools.sat.python import cp_model


def _object(value, keys, name):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError(f"{name} must contain exactly: {keys}")


def _integer(value, name, low=None, high=None):
    if type(value) is not int or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name} must be an integer" + (f" in {low}..{high}" if low is not None else ""))


def _strings(value, name):
    if type(value) is not list or any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"{name} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")


def _validate(snapshot):
    _object(snapshot, "now deadline resources tasks completed blocks", "snapshot")
    now, deadline = snapshot["now"], snapshot["deadline"]
    _integer(now, "now")
    _integer(deadline, "deadline")
    if not 1 <= deadline - now <= 180:
        raise ValueError("deadline must be 1..180 minutes after now")
    resources, tasks = snapshot["resources"], snapshot["tasks"]
    _strings(resources, "resources")
    if len(resources) > 8:
        raise ValueError("at most 8 resources are supported")
    if type(tasks) is not list or len(tasks) > 12:
        raise ValueError("tasks must be a list of at most 12 tasks")
    by_id = {}
    for task in tasks:
        _object(task, "id label duration resources after required priority", "task")
        if any(type(task[key]) is not str or not task[key].strip() for key in ("id", "label")):
            raise ValueError("task id and label must be nonempty strings")
        if task["id"] in by_id:
            raise ValueError("task ids must be unique")
        by_id[task["id"]] = task
        _integer(task["duration"], "duration", 1, 180)
        _integer(task["priority"], "priority", 1, 10)
        if type(task["required"]) is not bool:
            raise ValueError("required must be a boolean")
        _strings(task["resources"], "task resources")
        _strings(task["after"], "after")
        if not set(task["resources"]) <= set(resources):
            raise ValueError("task uses an unknown resource")
    for task in tasks:
        if not set(task["after"]) <= by_id.keys():
            raise ValueError("after contains an unknown task")
    visiting, visited = set(), set()

    def visit(task_id):
        if task_id in visiting:
            raise ValueError("task dependencies must be acyclic")
        if task_id in visited:
            return
        visiting.add(task_id)
        for parent_id in by_id[task_id]["after"]:
            visit(parent_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)
    _strings(snapshot["completed"], "completed")
    if not set(snapshot["completed"]) <= by_id.keys():
        raise ValueError("completed contains an unknown task")
    if type(snapshot["blocks"]) is not list:
        raise ValueError("blocks must be a list")
    for block in snapshot["blocks"]:
        _object(block, "resource start end", "block")
        if type(block["resource"]) is not str or block["resource"] not in resources:
            raise ValueError("block uses an unknown resource")
        _integer(block["start"], "block start")
        _integer(block["end"], "block end")
        if block["end"] <= block["start"]:
            raise ValueError("block end must be after start")


def plan_day(snapshot):
    """Return a schedule or an honest solver status; reject malformed input with ValueError.

    Completed tasks consume no future time. An unfinished task needs each unfinished
    prerequisite, even when that prerequisite is optional. Blocks are clipped to the
    planning window. Only omitted optional tasks in a found schedule are deferred.
    """
    _validate(snapshot)
    now, deadline = snapshot["now"], snapshot["deadline"]
    horizon = deadline - now
    completed = set(snapshot["completed"])
    tasks = [task for task in snapshot["tasks"] if task["id"] not in completed]
    model = cp_model.CpModel()
    intervals = {resource: [] for resource in snapshot["resources"]}
    variables, assumptions = {}, {}
    for task in tasks:
        task_id = task["id"]
        start = model.new_int_var(0, horizon, f"start:{task_id}")
        end = model.new_int_var(0, horizon, f"end:{task_id}")
        present = model.new_bool_var(f"present:{task_id}")
        interval = model.new_optional_interval_var(start, task["duration"], end, present, task_id)
        model.add(start == 0).only_enforce_if(present.Not())
        model.add(end == 0).only_enforce_if(present.Not())
        variables[task_id] = start, end, present
        for resource in task["resources"]:
            intervals[resource].append(interval)
        if task["required"]:
            model.add_assumption(present)
            assumptions[present.Index()] = task_id
    for task in tasks:
        start, _, present = variables[task["id"]]
        for parent_id in task["after"]:
            if parent_id not in completed:
                _, parent_end, parent_present = variables[parent_id]
                model.add_implication(present, parent_present)
                model.add(start >= parent_end).only_enforce_if(present)
    for resource in intervals:
        blocks = sorted((max(now, block["start"]), min(deadline, block["end"]))
                        for block in snapshot["blocks"] if block["resource"] == resource
                        and block["start"] < deadline and block["end"] > now)
        merged = []
        for start, end in blocks:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        for index, (start, end) in enumerate(merged):
            intervals[resource].append(model.new_fixed_size_interval_var(
                start - now, end - start, f"blocked:{resource}:{index}"))
        model.add_no_overlap(intervals[resource])
    finish = model.new_int_var(0, horizon, "finish")
    if tasks:
        model.add_max_equality(finish, [variables[task["id"]][1] for task in tasks])
    else:
        model.add(finish == 0)
    priority = sum(task["priority"] * variables[task["id"]][2] for task in tasks)
    start_total = sum(variables[task["id"]][0] for task in tasks)
    # One priority point beats every possible finish/start improvement combined.
    start_bound = len(tasks) * horizon
    finish_weight = start_bound + 1
    priority_weight = horizon * finish_weight + start_bound + 1
    model.maximize(priority * priority_weight - finish * finish_weight - start_total)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status == "MODEL_INVALID":
        raise RuntimeError("planner produced an invalid model")
    result = {"status": status, "now": now, "deadline": deadline, "scheduled": [],
              "deferred": [], "conflicts": [], "objective": None, "quality": ""}
    if status in ("OPTIMAL", "FEASIBLE"):
        for task in tasks:
            start, end, present = variables[task["id"]]
            if solver.value(present):
                result["scheduled"].append({"id": task["id"], "label": task["label"],
                    "start": now + solver.value(start), "end": now + solver.value(end),
                    "resources": list(task["resources"])})
            else:
                result["deferred"].append(task["id"])
        result["scheduled"].sort(key=lambda task: (task["start"], task["id"]))
        result["objective"] = {"priority_score": solver.value(priority),
                               "finish": now + solver.value(finish),
                               "start_total": solver.value(start_total)}
        result["quality"] = ("Maximum priority, earliest finish, and earliest total starts proven."
            if status == "OPTIMAL" else "Valid schedule found; maximum priority is not certified.")
    elif status == "INFEASIBLE":
        core = set(solver.sufficient_assumptions_for_infeasibility())
        result["conflicts"] = [task_id for literal, task_id in assumptions.items() if literal in core]
        result["quality"] = "Required tasks cannot all fit; conflicts are a sufficient set, not necessarily minimal."
    else:
        result["quality"] = "Search stopped without a schedule; feasibility is undetermined."
    return result
