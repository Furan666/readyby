"use strict";
const $ = (id) => document.getElementById(id);
const state = { view: null, pending: null, choices: [], busy: false, token: "", session: "", protocol: "2025-11-25", initialized: false, nextId: 0, trace: [] };
const clock = (minute) => Number.isFinite(minute) ? `${String(Math.floor((420 + minute) / 60)).padStart(2, "0")}:${String((420 + minute) % 60).padStart(2, "0")}` : "—";
const node = (tag, className, text) => { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; };
const validPlan = (plan) => plan && ["OPTIMAL", "FEASIBLE"].includes(plan.status);
const finishOf = (view) => view.plan.objective?.finish ?? view.earliest_plan?.objective?.finish ?? view.earliest_deadline;

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "notice error" : "notice";
  $("notice").hidden = !message;
}
function record(direction, payload) {
  state.trace.push({ direction, payload });
  if (state.trace.length > 80) state.trace.shift();
  $("trace-count").textContent = `${state.trace.filter((entry) => entry.direction === "Request").length} calls`;
  $("trace-output").textContent = state.trace.map((entry) => `${entry.direction}\n${JSON.stringify(entry.payload, null, 2)}`).join("\n\n");
}
async function rpcResponse(response, id) {
  if ((response.headers.get("content-type") || "").includes("text/event-stream")) {
    if (!response.body) throw new Error("The MCP response had no stream.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const readEvent = (event) => {
      const data = event.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, "")).join("\n");
      if (!data || data === "[DONE]") return null;
      const message = JSON.parse(data);
      record("Response", message);
      return message.id === id ? message : null;
    };
    try {
      while (true) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
        let separator;
        while ((separator = /\r?\n\r?\n/.exec(buffer))) {
          const event = buffer.slice(0, separator.index);
          buffer = buffer.slice(separator.index + separator[0].length);
          const message = readEvent(event);
          if (message) return message;
        }
        if (chunk.done) {
          if (buffer.trim()) { const message = readEvent(buffer); if (message) return message; }
          throw new Error("The MCP stream ended without this request's response.");
        }
      }
    } finally { await reader.cancel(); }
  }
  const message = await response.json();
  record("Response", message);
  const match = Array.isArray(message) ? message.find((item) => item.id === id) : message;
  if (!match || match.id !== id) throw new Error("The MCP response did not match this request.");
  return match;
}
async function rpc(method, params, notification = false) {
  const request = { jsonrpc: "2.0", method };
  if (params !== undefined) request.params = params;
  if (!notification) request.id = ++state.nextId;
  const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
  if (state.initialized) headers["MCP-Protocol-Version"] = state.protocol;
  if (state.session) headers["Mcp-Session-Id"] = state.session;
  record("Request", request);
  const response = await fetch("/mcp", { method: "POST", headers, body: JSON.stringify(request), credentials: "same-origin" });
  const session = response.headers.get("Mcp-Session-Id");
  if (session) state.session = session;
  if (!response.ok) throw new Error(`MCP request failed (${response.status}). Reload to reconnect.`);
  if (notification) { record("HTTP", { status: response.status, notification: method }); return null; }
  const message = await rpcResponse(response, request.id);
  if (message.error) throw new Error(message.error.message || "MCP returned an error.");
  return message.result;
}
async function tool(name, arguments_ = {}) {
  const result = await rpc("tools/call", { name, arguments: arguments_ });
  if (result.isError) throw new Error(result.content?.find((item) => item.type === "text")?.text || "The tool could not complete this request.");
  if (!result.structuredContent) throw new Error("The tool returned no structured content.");
  return result.structuredContent;
}
async function reviewPost(path, body) {
  const response = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-ReadyBy-Review": "1", "X-ReadyBy-Token": state.token }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Review request failed (${response.status}).`);
  return result;
}
function outcome(view) {
  const { plan, snapshot } = view;
  const finish = finishOf(view);
  if (plan.status === "INFEASIBLE") return { title: "This deadline needs a change.", detail: Number.isFinite(finish) ? `With every must-do task kept, the earliest departure is ${clock(finish)} — ${finish - snapshot.deadline} ${finish - snapshot.deadline === 1 ? "minute" : "minutes"} late.` : "The required tasks cannot all fit before the leave-by time. An earliest departure has not been established.", badge: "Deadline conflict", kind: "late" };
  if (!validPlan(plan)) return { title: "A plan has not been established.", detail: "The search did not establish feasibility. Review the reported assumptions before continuing.", badge: plan.status, kind: "neutral" };
  const spare = snapshot.deadline - finish;
  return { title: spare === 0 ? "It fits. There is no spare time." : "A clear path to leaving together.", detail: spare === 0 ? "The scheduled tasks finish at the leave-by time, under the reported durations. There is no buffer for another delay." : `The scheduled tasks finish at ${clock(finish)}, with ${spare} ${spare === 1 ? "minute" : "minutes"} to spare under the reported durations.`, badge: "Fits reported timings", kind: "ok" };
}
function renderTimeline(container, view) {
  container.replaceChildren();
  const plan = validPlan(view.plan) ? view.plan : view.earliest_plan;
  if (!validPlan(plan)) { container.append(node("p", "empty-timeline", "No valid schedule is available for this set of assumptions.")); return; }
  const start = view.snapshot.now;
  const end = Math.max(view.snapshot.deadline, plan.objective.finish, start + 10);
  const position = (minute) => Math.max(0, Math.min(100, (minute - start) / (end - start) * 100));
  const axis = node("div", "timeline-axis");
  const ticks = [start];
  for (let minute = Math.ceil((start + 1) / 5) * 5; minute < end; minute += 5) if (minute - start > 2 && end - minute > 2) ticks.push(minute);
  ticks.push(end);
  for (const minute of ticks) { const tick = node("span", "axis-tick", clock(minute)); tick.style.left = `${position(minute)}%`; axis.append(tick); }
  container.append(axis);
  for (const resource of view.snapshot.resources) {
    const row = node("div", "resource-row");
    row.append(node("div", "resource-label", resource));
    const track = node("div", "resource-track");
    for (const block of view.snapshot.blocks.filter((item) => item.resource === resource && item.end > start && item.start < end)) {
      const bar = node("div", "block-bar", "Unavailable");
      bar.style.left = `${position(block.start)}%`;
      bar.style.width = `${position(block.end) - position(block.start)}%`;
      bar.title = `${resource} reported unavailable ${clock(block.start)}–${clock(block.end)}`;
      bar.setAttribute("aria-label", bar.title);
      track.append(bar);
    }
    for (const task of plan.scheduled.filter((item) => item.resources.includes(resource))) {
      const bar = node("div", "task-bar", task.label);
      bar.style.left = `${position(task.start)}%`;
      bar.style.width = `${position(task.end) - position(task.start)}%`;
      bar.title = `${task.label}: ${clock(task.start)}–${clock(task.end)} · ${task.resources.join(" + ")}`;
      bar.setAttribute("aria-label", bar.title);
      track.append(bar);
    }
    const deadline = node("div", "deadline-line");
    deadline.style.left = `${position(view.snapshot.deadline)}%`;
    track.append(deadline);
    row.append(track);
    container.append(row);
  }
}
function taskLabel(id, view = state.view) { return view?.snapshot.tasks.find((task) => task.id === id)?.label || id; }
function summaryValue(field, value, draft) {
  if (["now", "deadline"].includes(field)) return clock(value);
  if (field === "completed") return value.length ? value.map((id) => taskLabel(id, draft)).join(", ") : "None reported";
  if (field === "blocks") return value.length ? value.map((block) => `${block.resource}: ${clock(block.start)}–${clock(block.end)}`).join("; ") : "None reported";
  if (field.endsWith(".duration")) return `${value} min`;
  return typeof value === "string" ? value : JSON.stringify(value);
}
function fieldLabel(field) {
  const labels = { now: "Reported time", deadline: "Leave by", completed: "Reported done", blocks: "Unavailable" };
  if (labels[field]) return labels[field];
  const [id, property] = field.split(".");
  return `${taskLabel(id)} · ${property === "duration" ? "duration" : property}`;
}
function showDraft(draft) {
  state.pending = draft;
  const section = $("preview-section");
  section.hidden = false;
  $("preview-title").textContent = draft.title || "Review the reported changes.";
  $("preview-revision").textContent = `Based on active revision ${draft.base_revision}`;
  $("summary-list").replaceChildren();
  for (const change of draft.summary) {
    const row = node("div", "summary-row");
    row.append(node("div", "summary-label", fieldLabel(change.field)));
    const value = node("div", "summary-value");
    value.append(node("span", "before", summaryValue(change.field, change.before, draft)), node("span", "arrow", "→"), node("span", "after", summaryValue(change.field, change.after, draft)));
    row.append(value);
    $("summary-list").append(row);
  }
  const result = outcome(draft);
  $("preview-outcome").textContent = `${result.title} ${result.detail}`;
  renderTimeline($("preview-timeline"), draft);
  syncControls();
  $("preview-title").focus({ preventScroll: true });
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}
function render() {
  if (!state.view) return;
  const view = state.view;
  const { snapshot, plan } = view;
  const result = outcome(view);
  const finish = finishOf(view);
  $("outcome-title").textContent = result.title;
  $("outcome-detail").textContent = result.detail;
  $("status-pill").textContent = result.badge;
  $("status-pill").className = `pill ${result.kind}`;
  $("now-display").textContent = clock(snapshot.now);
  $("deadline-display").textContent = clock(snapshot.deadline);
  $("finish-display").textContent = clock(finish);
  $("buffer-label").textContent = finish > snapshot.deadline ? "After deadline" : "Time to spare";
  $("buffer-display").textContent = Number.isFinite(finish) ? `${Math.abs(snapshot.deadline - finish)} min` : "—";
  $("saved-label").textContent = "Saved locally · reload to resume";
  $("revision-label").textContent = `Revision ${view.revision}`;
  $("timeline-caption").textContent = plan.status === "INFEASIBLE" ? "The current tasks cannot meet the deadline. This timeline shows the earliest departure if every must-do task is kept." : "Tasks sharing a person or the bathroom cannot overlap. Durations are reported assumptions.";
  $("solver-quality").textContent = plan.status === "INFEASIBLE" ? "No must-do task was dropped to make the times look better." : validPlan(plan) ? "All must-do tasks are included. No person or shared resource is double-booked." : "The search was inconclusive. That does not prove the departure is impossible.";
  renderTimeline($("active-timeline"), view);
  if (plan.status === "INFEASIBLE") {
    $("next-title").textContent = "Choose what changes.";
    $("next-copy").textContent = "A later departure or a shorter, household-approved wash can be previewed before you decide.";
  } else if (validPlan(plan) && plan.scheduled.length) {
    const first = plan.scheduled[0];
    const together = plan.scheduled.filter((task) => task.start === first.start);
    $("next-title").textContent = together.map((task) => task.label).join(" + ");
    $("next-copy").textContent = `${first.start === snapshot.now ? "Start at the reported time" : "Next start"}: ${clock(first.start)}. ${together.map((task) => `${task.label} (${task.resources.join(" + ")})`).join("; ")}.`;
  } else {
    $("next-title").textContent = validPlan(plan) ? "All reported tasks are complete." : "Review the assumptions.";
    $("next-copy").textContent = validPlan(plan) ? "No unfinished tasks remain in this morning." : "The solver has not established a valid schedule.";
  }
  $("completed-list").replaceChildren();
  if (!snapshot.completed.length) $("completed-list").append(node("p", "small-note", "No completed tasks reported yet."));
  for (const id of snapshot.completed) $("completed-list").append(node("span", "completed-item", taskLabel(id)));
  const previousTask = $("task-select").value;
  $("task-select").replaceChildren();
  for (const task of snapshot.tasks.filter((item) => !snapshot.completed.includes(item.id))) {
    const option = node("option", "", task.label); option.value = task.id; $("task-select").append(option);
  }
  if ([...$("task-select").options].some((option) => option.value === previousTask)) $("task-select").value = previousTask;
  updateDuration();
  $("deadline-input").value = clock(snapshot.deadline);
  $("tasks-list").replaceChildren();
  for (const task of snapshot.tasks) {
    const row = node("div", "task-detail-row");
    const status = snapshot.completed.includes(task.id) ? "Reported done" : plan.deferred.includes(task.id) ? "Deferred" : task.required ? "Required" : "Optional";
    row.append(node("strong", "", `${task.label} · ${task.duration} min · ${status}`));
    row.append(node("p", "", `${task.resources.join(" + ") || "No shared resource"}${task.after.length ? ` · After: ${task.after.map((id) => taskLabel(id)).join(", ")}` : " · No prerequisite"}`));
    $("tasks-list").append(row);
  }
  syncControls();
}
function updateDuration() {
  const task = state.view?.snapshot.tasks.find((item) => item.id === $("task-select").value);
  $("duration-input").value = task ? task.duration : "";
}
function syncControls() {
  const ready = Boolean(state.view) && !state.busy;
  for (const id of ["refresh-button", "reset-button", "deadline-input"]) $(id).disabled = !ready;
  const unfinished = ready && $("task-select").options.length > 0;
  for (const id of ["task-select", "duration-input"]) $(id).disabled = !unfinished;
  $("edit-button").disabled = !ready;
  const canReport = ready && state.view.snapshot.now <= 48 && !["alex_bathroom", "lunch"].every((id) => state.view.snapshot.completed.includes(id));
  $("report-button").disabled = !canReport;
  $("report-button").textContent = state.view?.snapshot.now >= 48 && !canReport ? "Interruption recorded" : "Report the interruption";
  $("choices-button").disabled = !ready || state.view.plan.status !== "INFEASIBLE";
  $("apply-button").disabled = !ready || !state.pending;
  $("discard-button").disabled = state.busy;
  for (const button of $("choice-cards").querySelectorAll("button")) button.disabled = !ready;
}
async function act(action) {
  if (state.busy) return;
  state.busy = true;
  notice("");
  syncControls();
  try { await action(); }
  catch (error) { notice(error.message || "The request could not be completed.", true); }
  finally { state.busy = false; syncControls(); }
}
async function readActive() { state.view = await tool("read_departure"); render(); }
function clearPreviews() {
  state.pending = null;
  state.choices = [];
  $("preview-section").hidden = true;
  $("choices-section").hidden = true;
  $("choice-cards").replaceChildren();
}
$("report-button").addEventListener("click", () => act(async () => {
  showDraft(await tool("draft_update", { changes: { now: 48, completed: ["alex_bathroom", "lunch"], blocks: [{ resource: "Bathroom", start: 48, end: 52 }] } }));
}));
$("choices-button").addEventListener("click", () => act(async () => {
  const result = await tool("recovery_choices");
  state.choices = result.choices;
  $("choices-reason").textContent = result.reason || "Preview a choice to review its exact changes.";
  $("choice-cards").replaceChildren();
  $("choices-section").hidden = false;
  for (const draft of state.choices) {
    const card = node("article", "choice-card");
    card.append(node("h3", "", draft.title), node("p", "choice-result", `Scheduled finish ${clock(finishOf(draft))} · leave by ${clock(draft.snapshot.deadline)}`));
    card.append(node("p", "", outcome(draft).detail));
    card.append(node("p", "", draft.summary.map((change) => `${fieldLabel(change.field)}: ${summaryValue(change.field, change.before, draft)} → ${summaryValue(change.field, change.after, draft)}`).join(". ")));
    const button = node("button", "button secondary", "Preview this choice ↗");
    button.type = "button";
    button.addEventListener("click", () => { if (!state.busy) showDraft(draft); });
    card.append(button);
    $("choice-cards").append(card);
  }
  $("choices-section").scrollIntoView({ behavior: "smooth", block: "start" });
}));
$("apply-button").addEventListener("click", () => act(async () => {
  const draft = state.pending;
  if (!draft) return;
  await reviewPost("/api/commit", { draft_id: draft.id, base_revision: draft.base_revision });
  clearPreviews();
  await readActive();
  notice("Reviewed changes applied. This is now the saved active morning.");
}));
$("discard-button").addEventListener("click", () => { if (!state.busy) { state.pending = null; $("preview-section").hidden = true; syncControls(); notice("Preview discarded. The active morning is unchanged."); } });
$("refresh-button").addEventListener("click", () => act(async () => { clearPreviews(); await readActive(); notice("Saved morning reloaded."); }));
$("reset-button").addEventListener("click", () => act(async () => { await reviewPost("/api/reset", {}); clearPreviews(); await readActive(); notice("The fictional morning has been reset to 07:40."); }));
$("task-select").addEventListener("change", updateDuration);
$("assumptions-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    const changes = {};
    const task = state.view.snapshot.tasks.find((item) => item.id === $("task-select").value);
    const duration = Number($("duration-input").value);
    if (task && duration !== task.duration) changes.task_changes = [{ id: task.id, duration }];
    const [hour, minute] = $("deadline-input").value.split(":").map(Number);
    const deadline = hour * 60 + minute - 420;
    if (deadline !== state.view.snapshot.deadline) changes.deadline = deadline;
    if (!Object.keys(changes).length) { notice("Change a duration or the leave-by time to preview an edit."); return; }
    showDraft(await tool("draft_update", { changes }));
  });
});
async function start() {
  state.busy = true;
  syncControls();
  try {
    const response = await fetch("/api/session", { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("The local review session could not be loaded.");
    const session = await response.json();
    if (typeof session.review_token !== "string") throw new Error("The local review session returned no token.");
    state.token = session.review_token;
    const initialized = await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "ReadyBy review client", version: "0.1.0" } });
    if (typeof initialized.protocolVersion !== "string") throw new Error("The MCP server did not negotiate a protocol version.");
    state.protocol = initialized.protocolVersion;
    state.initialized = true;
    await rpc("notifications/initialized", undefined, true);
    await readActive();
    $("connection-label").textContent = `MCP connected · ${state.protocol}`;
    $("connection-dot").className = "connection-dot connected";
  } catch (error) {
    $("connection-label").textContent = "MCP connection unavailable";
    $("connection-dot").className = "connection-dot failed";
    $("outcome-title").textContent = "The local morning could not be loaded.";
    $("outcome-detail").textContent = "Check that the ReadyBy server is running, then reload this page.";
    notice(error.message || "Connection failed.", true);
  } finally { state.busy = false; syncControls(); }
}
start();
