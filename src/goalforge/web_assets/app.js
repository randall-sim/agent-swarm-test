"use strict";
const $ = (id) => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("token")) {
  sessionStorage.setItem("goalforge-token", fragment.get("token"));
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("goalforge-token") || "";
let inspectorSignature = "";
let selected = null,
  cursor = 0,
  events = [],
  state = null,
  running = false,
  polling = false,
  selectedEvent = null,
  knownAgents = [],
  folder = "";
async function api(path, body) {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-GoalForge-Token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  return data;
}
function error(e) {
  $("error").textContent = e.message || String(e);
  $("error").hidden = false;
}
function clearError() {
  $("error").hidden = true;
}
function node(tag, text, className) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (className) e.className = className;
  return e;
}
function button(text, action, className) {
  const b = node("button", text, className);
  b.type = "button";
  b.onclick = () => Promise.resolve().then(action).catch(error);
  return b;
}
function inspect(title, sections) {
  const signature = JSON.stringify([title, sections]);
  if (signature === inspectorSignature) return;
  inspectorSignature = signature;
  $("detail-title").textContent = title;
  $("detail").replaceChildren();
  for (const [name, value] of Object.entries(sections)) {
    if (value === undefined) continue;
    const section = node("section", undefined, "detail-section");
    section.append(
      node("h3", name),
      node(
        "pre",
        typeof value === "string" ? value : JSON.stringify(value, null, 2),
      ),
    );
    $("detail").append(section);
  }
}
function cleanReply(reply) {
  if (!reply) return reply;
  return Object.fromEntries(
    Object.entries(reply).filter(([k]) => !k.startsWith("_native")),
  );
}
function inspectEvent(index) {
  selectedEvent = index;
  const event = events[index];
  const sections = {
    Event: {
      time: new Date(event.time * 1000).toLocaleString(),
      kind: event.kind,
      agent: event.agent_id,
      attempt: event.attempt,
    },
  };
  if (event.reply) {
    const reply = cleanReply(event.reply);
    sections[reply.final ? "Decision / explanation" : "Tool request"] =
      reply.final || reply;
    const next = events
      .slice(index + 1)
      .find(
        (e) =>
          e.agent_id === event.agent_id &&
          e.attempt === event.attempt &&
          ["role_reply", "tool_result"].includes(e.kind),
      );
    if (next?.kind === "tool_result") sections["Tool result"] = next.result;
  } else {
    sections.Details = event;
  }
  inspect(event.agent_id || "Orchestrator", sections);
}
function label(e) {
  if (e.kind === "role_reply") {
    const r = e.reply;
    return r.tool
      ? `${r.tool}${r.args?.path ? " · " + r.args.path : ""}`
      : "Decision recorded";
  }
  return (
    {
      role_started: "Started",
      role_finished: "Finished",
      worker_started: "Task assigned",
      worker_finished: "Changes ready",
      dispatch: "Dispatched parallel tasks",
      integration: "Combined worker changes",
      check:
        e.result?.returncode === 0
          ? "Verification passed"
          : "Verification failed",
      attempt: "Attempt " + e.outcome,
      log: e.message,
    }[e.kind] || e.kind.replaceAll("_", " ")
  );
}
function renderActivity() {
  const filter = $("filter").value;
  const visible = events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => e.kind !== "log" && (!filter || e.agent_id === filter));
  const box = $("timeline");
  const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 70;
  box.replaceChildren();
  for (const { e, i } of visible.slice(-400)) {
    const b = button("", () => inspectEvent(i), "event");
    const time = node("time", new Date(e.time * 1000).toLocaleTimeString());
    const text = node("div");
    text.append(
      node("strong", e.agent_id || "orchestrator"),
      node("span", label(e)),
    );
    b.append(time, text);
    box.append(b);
  }
  if (!visible.length) box.append(node("p", "Waiting for activity…", "muted"));
  if (bottom) box.scrollTop = box.scrollHeight;
  const latest = new Map();
  for (const e of events) {
    if (e.agent_id) latest.set(e.agent_id, e);
  }
  const ids = [...latest.keys()];
  if (ids.join() !== knownAgents.join()) {
    knownAgents = ids;
    $("filter").replaceChildren(
      new Option("All agents", ""),
      ...ids.map((id) => new Option(id, id)),
    );
    $("filter").value = filter;
  }
  $("agents").replaceChildren();
  for (const [id, e] of latest) {
    const work =
      running &&
      !["role_finished", "worker_finished", "worker_failed"].includes(e.kind);
    const b = button(
      "",
      () => {
        selectedEvent = null;
        const task = events.findLast(
          (x) => x.agent_id === id && x.kind === "worker_started",
        );
        const decision = events.findLast(
          (x) => x.agent_id === id && x.reply?.final,
        );
        inspect(id, {
          Assignment: task?.task,
          "Latest action": label(e),
          "Latest decision": decision?.reply.final,
        });
        $("filter").value = id;
        renderActivity();
      },
      "agent" + (work ? " working" : ""),
    );
    b.append(
      node("strong", id),
      node("small", work ? "● " + label(e) : label(e)),
    );
    $("agents").append(b);
  }
  if (selectedEvent !== null) inspectEvent(selectedEvent);
}
async function listRuns() {
  const data = await api("runs");
  $("runs").replaceChildren();
  for (const run of data) {
    const b = button(
      "",
      () => selectRun(run.id),
      "run-link" + (selected === run.id ? " active" : ""),
    );
    b.append(
      node("strong", run.goal),
      node("small", run.status + " · " + run.id.slice(0, 8)),
    );
    $("runs").append(b);
  }
  if (!data.length)
    $("runs").append(node("p", "Your runs will appear here.", "muted"));
}
async function selectRun(id) {
  selected = id;
  cursor = 0;
  events = [];
  selectedEvent = null;
  knownAgents = [];
  $("filter").value = "";
  $("composer").hidden = true;
  $("run-view").hidden = false;
  clearError();
  await poll();
  await listRuns();
}
async function poll() {
  if (!selected || polling) return;
  polling = true;
  const id = selected;
  try {
    const data = await api(
      "run?id=" + encodeURIComponent(id) + "&cursor=" + cursor,
    );
    if (selected !== id) return;
    state = data.state;
    running = data.running;
    cursor = data.cursor;
    events.push(...data.events);
    $("title").textContent = state.goal;
    $("status").textContent = data.stopping
      ? "Pausing…"
      : running
        ? "Running"
        : state.status;
    $("meta").textContent =
      `${state.repo}\nWorkspace: ${state.workspace} · ${state.model} · ${(data.live_usage || state.usage).requests || 0} requests · ${(data.live_usage || state.usage).input_tokens || 0} input / ${(data.live_usage || state.usage).output_tokens || 0} output tokens`;
    $("meta").style.whiteSpace = "pre-wrap";
    $("pause").disabled = !running || data.stopping;
    $("integrate").disabled =
      running || state.accepted_commit === state.start_commit;
    $("resume").disabled = running;
    $("followup").disabled = running;
    $("approvals").replaceChildren();
    for (const a of data.approvals) {
      const card = node("div", undefined, "approval");
      card.append(
        node("strong", "Command approval required"),
        node("pre", JSON.stringify(a.argv)),
        node(
          "p",
          "Runs in the agent worktree with your local account permissions.",
          "small",
        ),
      );
      const row = node("div", undefined, "row");
      row.append(
        button(
          "Allow once",
          () => api("approve", { id: a.id, allow: true }),
          "primary",
        ),
        button("Deny", () => api("approve", { id: a.id, allow: false })),
      );
      card.append(row);
      $("approvals").append(card);
    }
    renderActivity();
    $("connection").textContent = "Connected · updates every second";
  } catch (e) {
    error(e);
    $("connection").textContent = "Connection interrupted · retrying";
  } finally {
    polling = false;
  }
}
$("new").onclick = () => {
  selected = null;
  state = null;
  events = [];
  selectedEvent = null;
  $("composer").hidden = false;
  $("run-view").hidden = true;
  $("title").textContent = "What should we build?";
  $("status").textContent = "Ready";
  clearError();
  listRuns().catch(error);
};
$("goal-form").onsubmit = async (e) => {
  e.preventDefault();
  clearError();
  $("start").disabled = true;
  try {
    const data = await api("start", {
      repo: $("repo").value,
      goal: $("goal").value,
      checks: $("checks").value,
      model: $("model").value,
      workers: +$("workers").value,
      max_calls: +$("max-calls").value,
      iterations: +$("iterations").value,
      instructions: $("instructions").value,
      protected: $("protected").value,
    });
    await selectRun(data.id);
  } catch (e) {
    error(e);
  } finally {
    $("start").disabled = false;
  }
};
$("followup-form").onsubmit = async (e) => {
  e.preventDefault();
  clearError();
  $("resume").disabled = true;
  try {
    await api("resume", {
      id: selected,
      instructions: $("followup").value,
      workers: +$("resume-workers").value,
      max_calls: +$("resume-calls").value,
      iterations: +$("resume-iterations").value,
    });
    $("followup").value = "";
    await poll();
  } catch (e) {
    error(e);
    $("resume").disabled = false;
  }
};
$("pause").onclick = async () => {
  try {
    const result = await api("pause", { id: selected });
    inspect("Pausing run", { Status: result.message });
    await poll();
  } catch (e) {
    error(e);
  }
};
$("show-history").onclick = () => {
  selectedEvent = null;
  const sections = {};
  for (const attempt of state.history) {
    const prefix = "Attempt " + attempt.attempt + " · ";
    sections[prefix + "outcome"] = attempt.outcome;
    if (attempt.proposal) {
      sections[prefix + "plan"] = attempt.proposal.approach;
      sections[prefix + "acceptance"] = attempt.proposal.acceptance;
      sections[prefix + "assignments"] = attempt.proposal.tasks;
    }
    if (attempt.critique) sections[prefix + "critic"] = attempt.critique.reason;
    if (attempt.workers)
      sections[prefix + "workers"] = attempt.workers
        .map((w) => w.agent_id + ": " + w.summary)
        .join("\n\n");
    if (attempt.review) sections[prefix + "reviewer"] = attempt.review.reason;
    sections[prefix + "lesson"] = attempt.lesson;
  }
  if (!state.history.length)
    sections.Status =
      "No completed attempts yet. Click the live planner or critic actions to inspect decisions.";
  inspect("Plans & decisions", sections);
};
$("show-diff").onclick = async () => {
  try {
    selectedEvent = null;
    const data = await api("diff?id=" + encodeURIComponent(selected));
    inspect("Workspace changes", {
      "Diff from starting commit":
        data.diff ||
        "No integrated changes yet. During coding, inspect worker write_file actions.",
      ...(data.truncated
        ? { Notice: "Diff truncated to 200,000 characters" }
        : {}),
    });
  } catch (e) {
    error(e);
  }
};
async function openFile(id, path, start = 1) {
  const data = await api(
    "file?id=" +
      encodeURIComponent(id) +
      "&path=" +
      encodeURIComponent(path) +
      "&start=" +
      start,
  );
  inspect(path, {
    Contents: data.text,
    Lines: `${start}–${Math.min(start + 499, data.lines)} of ${data.lines}`,
  });
  const navigation = node("div", undefined, "row");
  if (start > 1)
    navigation.append(
      button("← Previous", () => openFile(id, path, Math.max(1, start - 500))),
    );
  if (start + 499 < data.lines)
    navigation.append(button("Next →", () => openFile(id, path, start + 500)));
  $("detail").append(navigation);
}
$("show-files").onclick = async () => {
  try {
    selectedEvent = null;
    const id = selected;
    const files = await api("files?id=" + encodeURIComponent(id));
    inspectorSignature = "";
    $("detail-title").textContent = "Workspace files";
    $("detail").replaceChildren(node("p", state.workspace, "muted small"));
    for (const path of files) {
      $("detail").append(button(path, () => openFile(id, path), "file-link"));
    }
  } catch (e) {
    error(e);
  }
};
$("integrate").onclick = async () => {
  if (
    !confirm(
      "Apply the accepted changes to " +
        state.repo +
        "? This fast-forwards its current Git branch. Review View changes first.",
    )
  )
    return;
  try {
    const result = await api("integrate", { id: selected });
    selectedEvent = null;
    inspect("Changes applied", result);
    await poll();
  } catch (e) {
    error(e);
  }
};
$("filter").onchange = renderActivity;
async function browse(path) {
  const data = await api("folder?path=" + encodeURIComponent(path));
  folder = data.path;
  $("folder-path").value = folder;
  $("folder-list").replaceChildren();
  for (const name of data.folders)
    $("folder-list").append(
      button("▸ " + name, () => browse(folder + "/" + name)),
    );
  $("folder-up").onclick = () => browse(data.parent).catch(error);
}
$("browse").onclick = async () => {
  try {
    await browse($("repo").value);
    $("folder-dialog").showModal();
  } catch (e) {
    error(e);
  }
};
$("folder-go").onclick = () => browse($("folder-path").value).catch(error);
$("folder-select").onclick = () => {
  $("repo").value = folder;
  $("folder-dialog").close();
};
$("folder-cancel").onclick = () => $("folder-dialog").close();
(async () => {
  try {
    const config = await api("config");
    $("repo").value = config.repo;
    $("model").value = config.model;
    if (!config.key_configured)
      error(
        Error(
          "Add LLM_API_KEY to the server .env file and restart before starting agents.",
        ),
      );
    await listRuns();
    $("connection").textContent = "Connected · local server";
  } catch (e) {
    error(e);
  }
})();
setInterval(() => poll(), 1000);
setInterval(() => listRuns().catch(() => {}), 5000);
