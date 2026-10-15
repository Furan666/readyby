# Host workflow

ReadyBy adds a bounded scheduling tool to an MCP host. The host can use language to collect facts, but the constraint solver decides whether a proposed plan fits. The included client uses explicit controls rather than pretending to be a language model.

1. Call `read_departure` to obtain the current revision, reported facts and planning result.
2. Ask for missing facts when needed. Task durations, completion, resource availability and acceptable alternatives belong to the household. Do not infer completion from the wall clock or silently shorten a task.
3. Call `draft_update` with the exact new facts. It stores a proposal, not a new active plan. Show the before/after diff and the resulting schedule.
4. If mandatory tasks cannot meet the deadline, call `recovery_choices`. The example household has explicitly supplied a quick-wash alternative. A late-departure choice keeps the original tasks. Both remain unapplied.
5. Present the available options with the departure time and buffer. If no feasible witness is found, distinguish proven infeasibility from an inconclusive search.
6. Open the local review client. A person reviews one exact proposal and chooses Apply. The MCP tool set deliberately has no commit operation. This is an interaction boundary, not a cryptographic guarantee of a human decision.
7. Re-read the saved revision before the next turn. Progress survives reloads and client reconnection. Stale previews are rejected rather than overwriting newer facts.

## Example interruption

All times are minutes after 07:00. The current time changes from40 to48. Completed becomes `["alex_bathroom","lunch"]`; the bathroom is unavailable48–52. Keeping an eight-minute shower means shower52–60, packing60–63 and shoes63–65. The three-minute wash instead permits wash52–55, packing55–58 and shoes58–60. The latter has no buffer.

There is no calendar write, device control, message, purchase or network service in this workflow. A future hosted/platform integration would require real authentication, account isolation and additional testing; it is outside this prototype.
