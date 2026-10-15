# ReadyBy

A local MCP tool for recovering a shared household departure plan after an interruption. It accounts for people, a shared bathroom, completed work and a fixed departure deadline. It shows alternatives for review instead of silently dropping mandatory tasks.

This is an original contest prototype with a fictional household, not a connected Alexa+ skill, a live voice assistant, or a validated commercial service. No LLM, Amazon account, API key, device, paid service or cloud deployment is needed to run it. An MCP host can call its actual Streamable HTTP tools; the included browser is a small real MCP client, not a scripted chatbot.

## Run

Use Python 3.12–3.14 (tested on Windows / Python 3.14.3). From this directory:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -B -m backend.server
```

On macOS/Linux, replace `.venv\Scripts\python` with `.venv/bin/python`. Open http://127.0.0.1:8877. Stop the server with Ctrl+C. It binds only to loopback. For the exact tested Windows dependency versions use `requirements-lock-win-py314.txt`; dependencies are installed from PyPI, not vendored.

## Try the recovery

1. At 07:40, the two people can finish by 07:59. The bathroom cannot be shared simultaneously.
2. Report the interruption; review the exact proposed change, then apply it. It is now 07:48; lunches and Alex's bathroom task are reported done; the bathroom is blocked until 07:52.
3. The original 08:00 deadline is proven infeasible. The earliest feasible mandatory departure is 08:05.
4. Compare the household's two supplied alternatives. Keep the original tasks and leave later, or explicitly choose a three-minute wash. Review before applying.
5. The shorter wash finishes at 08:00 with zero buffer. Reload: reported progress and the chosen plan persist in local SQLite.

Durations and completion are user-reported assumptions, not measurements. Tasks are not automatically executed. Time is an explicit planning input, not a live clock. A zero-buffer plan is fragile if a task overruns.

## MCP

Endpoint: `http://127.0.0.1:8877/mcp`; official Python SDK 2.3.0; negotiated protocol 2025-11-25; Streamable HTTP with JSON responses.

- `read_departure`: saved state and a fresh bounded solve.
- `draft_update(changes)`: preview progress/time/resource-block changes or explicit unfinished-task duration/label edits.
- `recovery_choices`: compare household-provided alternatives after proven infeasibility.

MCP tools cannot commit a change. The separate browser review endpoint applies the exact stored draft only at its original revision. Applying a draft twice, applying a stale draft or reusing a pre-reset draft is refused. Its same-origin checks and ephemeral review token are local CSRF protection, not proof of human presence, identity, or production authentication.

A general MCP host can connect locally using Streamable HTTP, read state and create proposals. Complete proposal review in the included UI. An external Alexa+ deployment would need approved hosting, TLS, authentication and actual platform/device testing; those are not implemented or claimed.

## Model and limits

OR-Tools CP-SAT schedules at most 12 tasks and 8 capacity-one resources over a 1–180-minute window. Dependencies form a DAG. Completed prerequisites consume no future time. Overlapping availability blocks are merged. All mandatory tasks must fit; optional tasks maximize the supplied priority sum, then minimize finish time and total starts. One solver call is capped at one second / one worker.

`OPTIMAL` certifies this bounded objective; `FEASIBLE` is a valid schedule without an optimality claim; `INFEASIBLE` is a proved mandatory conflict; `UNKNOWN` makes neither claim. Conflict IDs are a sufficient assumption set, not necessarily a minimal explanation. An earliest-departure search is reported only when each binary-search decision is proved infeasible or has a valid witness. The search limit is 180 minutes from the reported current time.

This demo supports one local household. It is not an internet service, a multi-user account system, a reliable alarm, or a medical/safety planner. No external network calls occur at runtime. Reset replaces only its own fictional household, not personal files.

## Verification

```powershell
.venv\Scripts\python -B -m unittest discover -s tests -v
```

Tests include independent raw schedule checks and small exhaustive objective oracles, interruption recovery, persistence/revision conflicts, and real MCP initialize/tools HTTP round trips. See `docs/VERIFICATION.md` for the tested build and UI evidence.

## Project notes

Built 15 October 2026 with OpenAI Codex assistance. Third-party dependencies retain their own licences; see `THIRD_PARTY.md`. The [Amazon Developer Hackathon entry](https://devpost.com/software/readyby) was submitted on 15 October 2026, with a [live demo](https://youtu.be/uUw60TQB1mc).
