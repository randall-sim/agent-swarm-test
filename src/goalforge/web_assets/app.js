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
  selectedAttempt = null,
  renderedAttempt = null,
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
function humanLabel(name) {
  const labels = {
    approach: "Approach",
    acceptance: "Success criteria",
    approved: "Plan approved",
    accept: "Changes accepted",
    complete: "Goal complete",
    reason: "Why",
    lesson: "Next steps / lessons",
    summary: "Implementation summary",
    files: "Assigned files",
    paths: "Changed files",
    argv: "Command",
    returncode: "Exit code",
    timed_out: "Timed out",
    content: "File contents",
    old: "Before",
    new: "After",
    text: "Output",
    input_tokens: "Input tokens",
    output_tokens: "Output tokens",
  };
  return (
    labels[name] ||
    name.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())
  );
}
function readable(value, key = "") {
  if (value === null || value === undefined)
    return node("p", "Not recorded", "muted");
  if (typeof value === "boolean")
    return node(
      "span",
      value ? "Yes" : "No",
      "decision-pill " + (value ? "yes" : "no"),
    );
  if (Array.isArray(value)) {
    if (key === "argv")
      return node(
        "pre",
        value.map((v) => (/\s/.test(v) ? '"' + v + '"' : v)).join(" "),
      );
    const list = node("div", undefined, "readable-list");
    for (const item of value) list.append(readable(item, key));
    if (!value.length) list.append(node("p", "None", "muted"));
    return list;
  }
  if (typeof value === "object") {
    const box = node("div", undefined, "readable-object");
    for (const [k, v] of Object.entries(value)) {
      if (k.startsWith("_native") || ["signature", "patch"].includes(k))
        continue;
      const field = node("div", undefined, "readable-field");
      field.append(node("h4", humanLabel(k)), readable(v, k));
      box.append(field);
    }
    return box;
  }
  if (
    [
      "content",
      "old",
      "new",
      "output",
      "text",
      "diff",
      "Contents",
      "Diff from starting commit",
      "Worker patch",
    ].includes(key)
  ) {
    const pre = node("pre");
    if (key.toLowerCase().includes("diff") || key === "Worker patch") {
      for (const line of String(value).split("\n"))
        pre.append(
          node(
            "span",
            line + "\n",
            line.startsWith("+")
              ? "diff-add"
              : line.startsWith("-")
                ? "diff-remove"
                : "diff-context",
          ),
        );
    } else pre.textContent = String(value);
    return pre;
  }
  return node("p", String(value), "readable-text");
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
    section.append(node("h3", name), readable(value, name));
    $("detail").append(section);
  }
}
const contextPreferences = new Map();
function contextGroup(parent, title, description) {
  const group = node("section", undefined, "context-group");
  group.append(node("h4", title), node("p", description, "muted small"));
  parent.append(group);
  return group;
}
function searchableContext(section, preference) {
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = "Search context, sources, files…";
  search.setAttribute("aria-label", "Search context");
  search.value = preference.query || "";
  const count = node("p", "", "muted small");
  count.setAttribute("aria-live", "polite");
  function filter() {
    preference.query = search.value;
    const query = search.value.trim().toLocaleLowerCase();
    const cards = [...section.querySelectorAll(".context-card")];
    let matches = 0;
    cards.forEach((card) => {
      card.hidden =
        !!query && !card.textContent.toLocaleLowerCase().includes(query);
      if (!card.hidden) matches++;
      if (query) card.open = !card.hidden;
    });
    count.textContent = query
      ? `${matches} of ${cards.length} context cards match in this request`
      : `${cards.length} context cards · search includes collapsed contents`;
    section.querySelectorAll(".context-group").forEach((group) => {
      group.hidden =
        !!query &&
        ![...group.querySelectorAll(".context-card")].some(
          (card) => !card.hidden,
        );
    });
  }
  search.oninput = filter;
  section.querySelector("h3").after(search, count);
  filter();
  return filter;
}
function contextBrowser(id, attempt) {
  const key = `${selected}:${attempt}:${id}`;
  if (!contextPreferences.has(key)) contextPreferences.set(key, {});
  const preference = contextPreferences.get(key);
  const snapshots = events.filter(
    (e) =>
      e.kind === "agent_context" && e.agent_id === id && e.attempt === attempt,
  );
  const section = node("section", undefined, "context-browser detail-section");
  section.append(node("h3", "Context supplied to this agent"));
  if (!snapshots.length) {
    const trace = events.filter((e) => (e.attempt ?? e._attempt) === attempt);
    const matching = trace.filter((e) => e.agent_id === id);
    const invoked = matching.some((e) => e.kind === "role_started");
    section.append(
      node(
        "p",
        invoked
          ? "This agent received context, but this server did not record exact input snapshots. The records below are recovered from saved events, not a reconstruction of the full prompt. Restart the updated server before your next run to capture exact inputs."
          : "This agent has not started in this attempt. No model input has been sent yet.",
        "muted",
      ),
    );
    section.append(
      node("p", `Run ${selected} · ${id} · Attempt ${attempt ?? "unknown"}`),
    );
    if (invoked) {
      const recovered = node("div", undefined, "context-pages");
      let group = contextGroup(
        recovered,
        "Initial inputs · recovered",
        "Known inputs supplied when this agent started; exact prompt unavailable.",
      );
      function card(title, source, value) {
        if (value === undefined) return;
        const d = document.createElement("details");
        d.className = "context-card";
        d.append(
          node("summary", title),
          node("p", "Source: " + source, "muted small"),
          readable(value, title),
        );
        group.append(d);
      }
      const history = state?.history?.find((h) => h.attempt === attempt);
      const planner = trace.findLast(
        (e) =>
          e.agent_id === "planner" &&
          e.kind === "role_reply" &&
          (e.reply?.final || e.reply?.title),
      );
      const plan = history?.proposal || planner?.reply?.final || planner?.reply;
      if (id !== "planner")
        card(
          "Planner proposal / shared interfaces",
          "Saved planner output for this attempt",
          plan,
        );
      const worker = matching.find((e) => e.kind === "worker_started");
      card("Assigned task", "Worker dispatch event", worker?.task);
      card("Workspace", "Worker dispatch event", worker?.workspace);
      card(
        "Earlier loop feedback",
        "Saved attempt history (exact prompt selection unavailable)",
        state?.history
          ?.filter((h) => h.attempt < attempt)
          .map((h) => ({
            attempt: h.attempt,
            outcome: h.outcome,
            lesson: h.lesson,
          })),
      );
      group = contextGroup(
        recovered,
        "Accumulated during this agent’s work",
        "Recorded results produced by tool execution during this invocation.",
      );
      let call;
      for (const e of matching) {
        if (e.kind === "role_reply") call = e.reply;
        if (e.kind === "tool_result")
          card(
            `Tool result · ${call?.tool || "tool"}${call?.args?.path ? " · " + call.args.path : ""}`,
            "Recorded workspace tool result; exact model-side truncation unavailable",
            e.result,
          );
      }
      section.append(recovered);
    }
    searchableContext(section, preference);
    return section;
  }
  const picker = document.createElement("select");
  picker.setAttribute("aria-label", "Agent context request");
  snapshots.forEach((e, i) => {
    const option = document.createElement("option");
    option.value = i;
    option.textContent = `Loop ${e.attempt ?? "unknown"} · Request ${e.request_number}`;
    picker.append(option);
  });
  const body = node("div", undefined, "context-pages");
  let applySearch = () => {};
  function show() {
    const e = snapshots[Number(picker.value)];
    body.replaceChildren(
      node(
        "p",
        `Run ${e.run_id} · ${e.agent_id} · Loop ${e.attempt ?? "unknown"}`,
      ),
      node("p", `Workspace: ${e.workspace}`, "small"),
      node("p", e.note, "muted small"),
    );
    const initial = contextGroup(
      body,
      "Initial inputs",
      "Supplied when this agent started: instructions, assignment, and any context passed from earlier agents or attempts.",
    );
    const generated = contextGroup(
      body,
      "Accumulated during this agent’s work",
      "This agent’s earlier messages, tool results, and corrections still included in the selected request.",
    );
    let group = initial;
    function card(title, source, value) {
      const d = document.createElement("details");
      d.className = "context-card";
      d.append(
        node("summary", title),
        node("p", "Source: " + source, "muted small"),
        readable(value, title),
      );
      group.append(d);
    }
    e.messages.forEach((m, i) => {
      if (i === 0)
        card(
          "System instructions",
          "GoalForge protocol and role instructions",
          m.content,
        );
      else if (i === 1) {
        let context;
        try {
          context = JSON.parse(m.content);
        } catch {
          context = { context: m.content };
        }
        Object.entries(context).forEach(([key, value]) =>
          card(
            humanLabel(key),
            e.sources[key] || "Orchestrator context",
            value,
          ),
        );
      } else {
        group = generated;
        let value = m.content;
        if (m.tool_calls)
          value = m.tool_calls.map((c) => {
            let args = c.function.arguments;
            try {
              args = JSON.parse(args);
            } catch {}
            return { tool: c.function.name, arguments: args };
          });
        else if (typeof value === "string") {
          try {
            value = JSON.parse(
              value.replace(/^Tool result \(untrusted\):\n/, ""),
            );
          } catch {}
        }
        card(
          `Conversation message ${i - 1} · ${m.role}`,
          m.role === "assistant"
            ? "Earlier response from this agent"
            : m.role === "tool" || String(m.content).startsWith("Tool result")
              ? "Workspace tool execution returned to this agent"
              : "Orchestrator validation / correction",
          value,
        );
      }
    });
    group = initial;
    if (e.tool_definitions?.length)
      card(
        "Available tools",
        "GoalForge native tool schemas",
        e.tool_definitions,
      );
    applySearch();
  }
  picker.value =
    preference.request !== undefined
      ? Math.min(preference.request, snapshots.length - 1)
      : snapshots.length - 1;
  picker.onchange = () => {
    preference.request = Number(picker.value);
    show();
  };
  show();
  section.append(picker, body);
  applySearch = searchableContext(section, preference);
  return section;
}
let selectedAgent = null;
function inspectAgent(id, title, trace, status, detail) {
  const matching = trace.filter((e) => e.agent_id === id);
  const final = matching.findLast((e) => e.reply?.final)?.reply.final;
  const report = matching.findLast((e) => e.kind === "worker_finished");
  const task =
    matching.find((e) => e.kind === "worker_started")?.task ||
    detail?.Assignment;
  const sections = { Status: status };
  if (task) {
    sections.Assignment = task.title;
    sections["Planned approach"] = task.approach;
    sections["Success criteria"] = task.acceptance;
    sections["Assigned files"] = task.files;
  }
  if (final) {
    if (id === "planner") {
      sections.Plan = final.title;
      sections.Approach = final.approach;
      sections["Success criteria"] = final.acceptance;
      sections["Delegated tasks"] = final.tasks;
    } else if (id === "critic") {
      sections.Decision = final.approved
        ? "Approved the plan"
        : "Rejected the plan";
      sections["Why this decision"] = final.reason;
    } else if (id === "reviewer") {
      sections.Decision = final.accept
        ? "Accepted the changes"
        : "Rejected the changes";
      sections["Goal complete"] = final.complete;
      sections["Why this decision"] = final.reason;
      sections["Next steps / lessons"] = final.lesson;
    } else sections["Implementation summary"] = final.summary;
  }
  if (id.startsWith("coder")) {
    if (report) sections["Changed files"] = report.paths;
    const edits = [];
    matching.forEach((e) => {
      const r = e.reply;
      if (!r || !["write_file", "replace_text", "delete_file"].includes(r.tool))
        return;
      const index = trace.indexOf(e);
      const result = trace
        .slice(index + 1)
        .find(
          (x) =>
            x.agent_id === id && ["role_reply", "tool_result"].includes(x.kind),
        );
      edits.push({
        file: r.args.path,
        action: {
          write_file: "Write file",
          replace_text: "Replace text",
          delete_file: "Delete file",
        }[r.tool],
        status:
          result?.kind !== "tool_result"
            ? "Awaiting result"
            : result.result.error
              ? "Failed: " + result.result.error
              : "Tool completed",
        ...(r.tool === "replace_text"
          ? { old: r.args.old, new: r.args.new }
          : r.tool === "write_file"
            ? { content: r.args.content }
            : {}),
      });
    });
    sections["File edits in this attempt"] = edits.length
      ? edits
      : "No direct file-tool edits recorded. Changes made through terminal commands are shown in the worker diff.";
    sections["About these changes"] =
      "Tool edits are provisional until checks and review accept the combined result.";
  }
  if (!final)
    sections["Recorded explanation"] =
      "No final explanation yet. The activity list shows the actions recorded so far.";
  if (!matching.length && detail) Object.assign(sections, detail);
  sections["Run / loop"] = `${selected} · Loop ${viewedAttempt() ?? "unknown"}`;
  // Include snapshot count in the signature so live context stays current.
  sections["Context requests recorded"] = matching.filter(
    (e) => e.kind === "agent_context",
  ).length;
  inspect(title, sections);
  if (!$("detail").querySelector(".context-browser"))
    $("detail").prepend(contextBrowser(id, viewedAttempt()));
  if (report?.patch && id.startsWith("coder-")) {
    const patchButton = button(
      "View this worker’s diff",
      async () => {
        const data = await api(
          "patch?id=" +
            encodeURIComponent(selected) +
            "&attempt=" +
            report.attempt +
            "&agent=" +
            encodeURIComponent(id),
        );
        selectedAgent = null;
        inspect(title + " · changes", {
          "Implementation summary": report.summary,
          "Changed files": report.paths,
          "Worker patch": data.available
            ? data.patch || "No changes in this patch."
            : "The worker patch is not available yet.",
        });
      },
      "worker-diff",
    );
    if (!$("detail").querySelector(".worker-diff"))
      $("detail").prepend(patchButton);
  }
}
function cleanReply(reply) {
  if (!reply) return reply;
  return Object.fromEntries(
    Object.entries(reply).filter(([k]) => !k.startsWith("_native")),
  );
}
function inspectEvent(index) {
  selectedAgent = null;
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
  if (event.kind === "agent_context") {
    inspect("Agent input context", {
      Run: event.run_id,
      Agent: event.agent_id,
      Loop: event.attempt,
    });
    $("detail").querySelector(".context-browser")?.remove();
    const browser = contextBrowser(event.agent_id, event.attempt);
    $("detail").prepend(browser);
    const picker = browser.querySelector("select");
    if (picker) {
      const snapshots = events.filter(
        (e) =>
          e.kind === "agent_context" &&
          e.agent_id === event.agent_id &&
          e.attempt === event.attempt,
      );
      picker.value = snapshots.indexOf(event);
      picker.onchange();
    }
    return;
  }
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
      agent_context: `Context supplied · request ${e.request_number}`,
      provider_wait: `Waiting for shared API cooldown · ${e.seconds}s`,
      provider_retry: `API ${e.status} · retry ${e.retry}/${e.max_retries} after ${e.seconds}s`,
      provider_request: e.retry
        ? `Retrying API request (${e.retry})`
        : "Calling model",
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
function viewedAttempt() {
  return selectedAttempt ?? state?.attempt ?? 0;
}
function chooseAttempt(value) {
  selectedAttempt = value;
  selectedEvent = null;
  selectedAgent = null;
  inspectorSignature = "";
  $("filter").value = "";
  inspect("Attempt " + viewedAttempt(), {
    Status: "Select an agent or action to inspect this attempt.",
  });
  renderActivity();
}
function renderAttemptNavigation() {
  const nav = $("attempt-navigation");
  const attempts = [
    ...new Set([
      0,
      state?.attempt ?? 0,
      ...events.map((e) => e.attempt ?? e._attempt ?? 0),
      ...(state?.history || []).map((h) => h.attempt),
    ]),
  ].sort((a, b) => a - b);
  const signature = JSON.stringify([attempts, selectedAttempt, state?.attempt]);
  if (nav.dataset.signature === signature) return;
  nav.dataset.signature = signature;
  nav.replaceChildren();
  const picker = document.createElement("select");
  picker.id = "attempt-select";
  picker.setAttribute("aria-label", "View attempt");
  picker.append(new Option("Follow latest attempt", "latest"));
  for (const attempt of attempts)
    picker.append(
      new Option(
        attempt === 0 ? "Baseline" : `Attempt ${attempt}`,
        String(attempt),
      ),
    );
  picker.value = selectedAttempt === null ? "latest" : String(selectedAttempt);
  picker.onchange = () =>
    chooseAttempt(picker.value === "latest" ? null : Number(picker.value));
  const previous = button("← Previous", () =>
    chooseAttempt(attempts[attempts.indexOf(viewedAttempt()) - 1]),
  );
  previous.disabled = attempts.indexOf(viewedAttempt()) <= 0;
  const next = button("Next →", () =>
    chooseAttempt(attempts[attempts.indexOf(viewedAttempt()) + 1]),
  );
  next.disabled = attempts.indexOf(viewedAttempt()) >= attempts.length - 1;
  nav.append(previous, picker, next);
}
function renderActivity() {
  if (renderedAttempt !== viewedAttempt()) {
    renderedAttempt = viewedAttempt();
    selectedAgent = null;
    selectedEvent = null;
    inspect("Attempt " + viewedAttempt(), {
      Status: "Select an agent or action to inspect this attempt.",
    });
  }
  renderAttemptNavigation();
  const filter = $("filter").value;
  const visible = events
    .map((e, i) => ({ e, i }))
    .filter(
      ({ e }) =>
        (e.attempt ?? e._attempt) === viewedAttempt() &&
        e.kind !== "log" &&
        (!filter || e.agent_id === filter),
    );
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
  renderGraph();
  if (selectedEvent !== null) inspectEvent(selectedEvent);
}
function renderGraph() {
  const host = $("agents");
  host.replaceChildren();
  const trace = events.filter(
    (e) => (e.attempt ?? e._attempt) === viewedAttempt(),
  );
  const liveAttempt = running && viewedAttempt() === state?.attempt;
  const dispatch = trace.find((e) => e.kind === "dispatch");
  const workerIds = dispatch
    ? dispatch.tasks.map((_, i) => "coder-" + (i + 1))
    : trace.some((e) => e.agent_id === "coder")
      ? ["coder"]
      : [];
  const ids = workerIds.length ? workerIds : ["workers"];
  const width = Math.max(480, ids.length * 160);
  const ends = new Map();
  const intervals = [];
  for (const e of trace) {
    if (e.kind === "worker_started") ends.set(e.agent_id, e.time);
    if (
      ["worker_finished", "worker_failed"].includes(e.kind) &&
      ends.has(e.agent_id)
    ) {
      intervals.push([ends.get(e.agent_id), e.time]);
      ends.delete(e.agent_id);
    }
  }
  for (const time of ends.values())
    intervals.push([time, trace.at(-1)?.time || time]);
  const points = intervals
    .filter(([a, b]) => b > a)
    .flatMap(([a, b]) => [
      [a, 1],
      [b, -1],
    ])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let concurrent = 0,
    peak = 0;
  for (const [, delta] of points) {
    concurrent += delta;
    peak = Math.max(peak, concurrent);
  }
  const active = liveAttempt ? ends.size : 0;
  const heading = node("div", undefined, "graph-heading");
  heading.append(
    node("strong", "Agent flow · attempt " + viewedAttempt()),
    node(
      "span",
      active > 1
        ? `${active} workers running in parallel`
        : peak > 1
          ? `Peak: ${peak} workers overlapped`
          : "Parallelism appears when workers overlap",
      "parallel-badge",
    ),
  );
  host.append(heading);
  const outcome = state?.history?.findLast(
    (h) => h.attempt === viewedAttempt(),
  );
  if (outcome)
    host.append(
      node(
        "p",
        `${outcome.outcome}: ${outcome.lesson || ""}`,
        "attempt-outcome",
      ),
    );
  if (state?.last_error && viewedAttempt() === state.attempt)
    host.append(node("p", state.last_error, "attempt-outcome"));
  const scroll = node("div", undefined, "graph-scroll");
  const canvas = node("div", undefined, "graph-canvas");
  canvas.style.width = width + "px";
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} 530`);
  svg.setAttribute("aria-hidden", "true");
  canvas.append(svg);
  function edge(x1, y1, x2, y2, on = false) {
    const path = document.createElementNS(ns, "path");
    const middle = (y1 + y2) / 2;
    path.setAttribute("d", `M ${x1} ${y1} V ${middle} H ${x2} V ${y2}`);
    path.setAttribute("class", on ? "flow-edge live" : "flow-edge");
    svg.append(path);
    const arrow = document.createElementNS(ns, "path");
    arrow.setAttribute(
      "d",
      `M ${x2 - 4} ${y2 - 6} L ${x2} ${y2} L ${x2 + 4} ${y2 - 6}`,
    );
    arrow.setAttribute("class", on ? "flow-edge live" : "flow-edge");
    svg.append(arrow);
  }
  function addNode(id, title, x, y, matching, detail) {
    const last = matching.at(-1);
    const finished =
      last &&
      ["role_finished", "worker_finished", "integration", "check"].includes(
        last.kind,
      );
    const failed =
      last &&
      (last.kind === "worker_failed" ||
        (last.kind === "check" &&
          (last.result.returncode !== 0 || last.result.timed_out)));
    const isActive = liveAttempt && last && !finished && !failed;
    const status = failed
      ? "Failed"
      : finished
        ? "Done"
        : isActive
          ? "Running"
          : last
            ? "Stopped"
            : "Waiting";
    const b = button(
      "",
      () => {
        selectedEvent = null;
        selectedAgent = null;
        selectedAgent = { id, title };
        inspectAgent(id, title, trace, status, detail);
        $("filter").value = knownAgents.includes(id) ? id : "";
        renderActivity();
      },
      "graph-node " +
        (isActive ? "working" : failed ? "failed" : finished ? "done" : ""),
    );
    b.style.left = x - 68 + "px";
    b.style.top = y + "px";
    b.append(
      node("strong", title),
      node("small", status + (isActive ? " · " + label(last) : "")),
    );
    b.setAttribute("aria-label", title + ": " + status);
    canvas.append(b);
    if (selectedAgent?.id === id)
      inspectAgent(id, title, trace, status, detail);
  }
  const cx = width / 2;
  const roles = (id) =>
    trace.filter((e) => e.agent_id === id && e.kind !== "log");
  edge(cx, 58, cx, 85);
  addNode("planner", "Planner", cx, 0, roles("planner"));
  addNode("critic", "Critic", cx, 85, roles("critic"));
  ids.forEach((id, i) => {
    const x = ((i + 0.5) * width) / ids.length;
    edge(cx, 143, x, 190, active > 0 && ends.has(id));
    edge(x, 248, cx, 300, active > 0 && ends.has(id));
    addNode(id, id === "workers" ? "Coding workers" : id, x, 190, roles(id), {
      Assignment: dispatch?.tasks[i],
      Workspace: trace.find(
        (e) => e.kind === "worker_started" && e.agent_id === id,
      )?.workspace,
    });
  });
  edge(cx, 358, cx, 385);
  edge(cx, 443, cx, 470);
  addNode(
    "integration",
    "Integration",
    cx,
    300,
    trace.filter(
      (e) =>
        e.kind === "integration" ||
        (workerIds[0] === "coder" &&
          e.agent_id === "coder" &&
          e.kind === "role_finished"),
    ),
    { Purpose: "Combine all worker patches after every worker finishes." },
  );
  addNode(
    "checks",
    "Checks",
    cx,
    385,
    trace.filter((e) => ["check_started", "check"].includes(e.kind)),
    {
      Purpose:
        "Run the required verification commands on the combined changes.",
    },
  );
  addNode("reviewer", "Reviewer", cx, 470, roles("reviewer"));
  scroll.append(canvas);
  host.append(scroll);
  host.append(
    node(
      "p",
      "Branches are separate worker workspaces. They join only after all workers finish. Click a node to inspect it.",
      "muted small",
    ),
  );
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
  renderedAttempt = null;
  selectedAttempt = null;
  cursor = 0;
  events = [];
  selectedEvent = null;
  selectedAgent = null;
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
    let eventAttempt = events.at(-1)?._attempt ?? 0;
    for (const e of data.events) {
      eventAttempt = e.attempt ?? eventAttempt;
      events.push({ ...e, _attempt: eventAttempt });
    }
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
  selectedAttempt = null;
  state = null;
  events = [];
  selectedEvent = null;
  selectedAgent = null;
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
  selectedAgent = null;
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
    selectedAgent = null;
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
    selectedAgent = null;
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
    selectedAgent = null;
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
    $("server-update").hidden = config.context_capture_version >= 1;
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

function expandInspector(expanded) {
  document.body.classList.toggle("inspector-expanded", expanded);
  $("expand-inspector").textContent = expanded
    ? "Restore sidebar ↙"
    : "Expand panel ↗";
  $("expand-inspector").setAttribute("aria-expanded", String(expanded));
}
$("expand-inspector").onclick = () =>
  expandInspector(!document.body.classList.contains("inspector-expanded"));
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    document.body.classList.contains("inspector-expanded")
  ) {
    expandInspector(false);
    $("expand-inspector").focus();
  }
});
