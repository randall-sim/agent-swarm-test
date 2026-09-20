# GoalForge

A small Python coding agent with a CLI and local web UI, inspired by **RecEvolve**. Give it a coding goal, a repository, verification commands, and an LLM API key. It inspects code, proposes a change, critiques the plan, edits files, runs checks, and reviews the result. Useful changes accumulate on a separate Git branch.

Use it for bug fixes, small features, refactoring, tests, and documentation. It is a working starter implementation, not a reproduction of the paper's production infrastructure or a claim of equivalent results.

- Python 3.10+ and Git; **no third-party Python runtime dependencies**.
- A planner and critic coordinate **up to 3 concurrent coding workers by default**, followed by integration and review.
- Configurable Chat Completions-compatible endpoint and model.
- Persistent run history, separate worker worktrees, and one shared request budget.
- File editing tools plus terminal commands; command approval by default.

## Install

From this directory, on macOS/Linux/WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
goalforge --help
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
goalforge --help
```

If activation is unavailable, invoke `.venv\Scripts\python.exe -m goalforge` after installing with that same Python. You can also run without installation by adding `src` to `PYTHONPATH` and using `python -m goalforge`.

## Local web UI

Launch GoalForge from its installed environment:

```bash
source .venv/bin/activate
goalforge serve /path/to/your/project
```

Or, without activating the environment, run `.venv/bin/python -m goalforge serve /path/to/your/project` from the GoalForge directory. The server prints its local URL without opening a browser. Open that link yourself, or use `--open` to launch your browser automatically. `--no-open` remains supported. Use `--port 8766` if the default port is busy. In WSL, paste the printed URL into your Windows browser.

The server reads `.env` from the directory where you launch it. To launch from elsewhere, pass `--env-file /path/to/goalforge/.env`. Your API key stays on the Python server; the browser receives only a flag indicating whether it is configured. Restart the server after editing `.env`.

The UI provides:

- **New goal:** choose a local folder using Browse, enter instructions and required verification commands, select the model, and set the worker count, request budget, and attempt limit. Commands use argument syntax, not shell pipelines. Use an absolute Python executable path when the target project needs its own virtual environment.
- **Live activity:** updates every second from the persisted event log. A clickable node graph shows the latest attempt: planner → critic → parallel workers → integration → checks → reviewer. Active workers and connections are highlighted; the overlap badge reports concurrent workers and the peak overlap after completion. Select an agent node to inspect its assignment and filter the timeline. Agent detail panels render plans, assignments, approval reasons, implementation summaries, and review outcomes as readable sections. Coder panels include changed files, recorded edits, and a button to inspect their exact worker diff. Click an action to see its tool arguments and result, task assignment, or stated decision. These are observable actions and explicit summaries, not private model reasoning.
- **Approvals:** approve or deny each model-requested terminal command in the browser. Your configured verification commands run automatically when you start the goal. Commands execute with your local account's permissions; worktrees are not a sandbox.
- **Review:** inspect planner/critic/reviewer decisions, browse the integrated workspace files, and view changes relative to the starting commit. During parallel coding, worker edits remain in separate worktrees until integration; inspect `write_file`/`replace_text` actions to see those edits live.
- **Pause and continue:** pause waits for in-flight API requests or commands to finish (bounded by their timeouts), then discards unaccepted work and retains reviewed commits. Resume with a new budget, or add follow-up instructions in the UI. Pause first before changing instructions. Closing the browser does not stop the agents; Ctrl-C in the server terminal requests a pause and waits for cleanup.
- **Apply to original folder:** after reviewing accepted changes, explicitly confirm a fast-forward into the original repository's current branch. The original folder must be clean and its branch must support that fast-forward; conflicts or divergent history are left for you to resolve. No automatic push occurs.
- **Saved runs:** reopen earlier CLI or UI runs from the sidebar, including their original event history. Reopening the page reconstructs the timeline from disk.

The target must be a Git repository with a committed HEAD and clean working tree. Keep run storage outside the repository. The server executes one goal at a time, with up to eight parallel coding workers within that goal; it reuses the CLI engine, checks, protected-path rules, request budget, and isolated worktrees.

This is a local single-user interface: it binds only to `127.0.0.1`, requires a random session token for its API, and rejects foreign Host/Origin requests. Open the full URL printed by the server; the token is stored for that browser tab and removed from the address bar. It is not designed for public hosting or team authentication. Files and log text render as text, not executable HTML. No frontend build step or third-party Python runtime dependency is required.

## Configure your LLM

Edit the `.env` file in the GoalForge directory and set:

```dotenv
LLM_API_KEY=your-api-key
LLM_MODEL=your-model-id
LLM_BASE_URL=https://your-provider/v1
```

`LLM_MODEL` selects the model by its exact provider model ID. `LLM_BASE_URL` selects the provider's Chat Completions-compatible API base URL, usually ending in `/v1`. GoalForge appends `/chat/completions`; do not include that suffix yourself.

For a fresh clone, create the local file with `cp .env.example .env` (macOS/Linux/WSL) or `Copy-Item .env.example .env` (PowerShell). `.env` and `.env.*` are ignored by Git, except the safe `.env.example` template. Never put a real key in the template.

GoalForge automatically reads `.env` from the **directory where you launch the command**, including when `--repo` points to another project. It does not search parent directories or automatically read the target repository's configuration. From another directory, select it explicitly:

```bash
goalforge run "Fix empty input handling" --repo /path/to/project --env-file /path/to/goalforge/.env --check "python -m unittest discover -s tests"
goalforge resume RUN_ID --env-file /path/to/goalforge/.env
```

Configuration priority is **CLI flags → existing environment variables → `.env` → saved run settings (on resume)**. The same `--env-file` option works with `list`, `status`, and `history`. An explicitly requested missing file is an error; a missing default `.env` is fine. `GOALFORGE_HOME` can also be set in `.env` to choose where run state is saved.

Values may be unquoted or enclosed in single/double quotes. Blank lines, `#` comment lines, inline comments after whitespace, and optional `export NAME=value` are supported. Values are literal: no shell execution, variable expansion, escape decoding, or multiline values. Quote a value if it contains whitespace followed by `#`.

The key stays in your local `.env`; it is not saved in run configuration. Settings loaded from the file are not exported into subprocess environments. Alternatively, supply `LLM_API_KEY` through the environment or leave it blank for a hidden terminal prompt. `--key-env NAME` selects another key variable, from either source. Noninteractive runs require a configured key.

For OpenAI, a low-cost starting configuration is `LLM_MODEL=gpt-5.4-mini` and `LLM_BASE_URL=https://api.openai.com/v1`.

The client sends `model`, `messages`, and `max_completion_tokens` for `api.openai.com` (`max_tokens` for other compatible providers), and registers native function tools for OpenAI. Tool results are returned with matching call IDs, and each role finishes through a schema-checked `finish` function. Other compatible providers use the fallback text-JSON protocol in `choices[0].message.content`; choose a model that follows those instructions. Native Anthropic Messages, Gemini, and other incompatible API shapes require a compatible gateway or an adapter in `provider.py`. Providers that require different token parameters also need an adapter. No automatic provider detection is attempted.

For a local compatible server, a base URL such as `http://localhost:8000/v1` is allowed; set a dummy key if the server does not authenticate. Remote endpoints require HTTPS. HTTP redirects are refused to avoid forwarding credentials.

## Run a coding goal

Your target repository must have an initial commit and a clean working tree. Commit or stash existing edits before starting. Install that project's dependencies in an environment available to your commands.

```bash
goalforge run "Handle empty input in average(): raise ValueError with a useful message, and preserve normal results" \
  --repo /path/to/project \
  --check "python -m unittest discover -s tests -v" \
  --protect tests \
  --iterations 5
```

PowerShell uses a backtick for line continuation, or put the command on one line:

```powershell
goalforge run "Fix empty input handling" --repo C:\code\project --check "python -m unittest discover -s tests -v" --protect tests
```

The agent operates in a new worktree. Your original checkout remains at its original commit. GoalForge prints a run ID, worktree path, branch, phase progress, check results, and final status.

`--check` is optional and repeatable; omission defaults to unittest discovery in `tests/`. All configured checks must exit zero, without timing out, before a candidate can be kept. Configured or default checks run automatically. A failing baseline is allowed so the agent can fix existing failures; every accepted candidate must pass all checks.

Use `--protect` for acceptance tests, fixtures, or configuration the agent should not change. Paths are repository-relative; directories protect their descendants, and globs are supported. Protecting the entire `tests` directory also prevents adding tests there. To allow new tests while preserving existing ones, protect individual acceptance files instead:

```bash
goalforge run "Add ISO date input to the parser and test invalid dates" \
  --repo /path/to/project \
  --check "python -m unittest discover -s tests" \
  --check "python scripts/check_api_compatibility.py" \
  --protect tests/test_acceptance.py \
  --protect scripts/check_api_compatibility.py \
  --directive examples/directive.md
```

The optional directive supplies your coding conventions and constraints. Goals and directives are sent to your LLM provider, along with the selected repository context.

Checks and model commands are argument lists executed without an implicit shell. `&&`, pipes, glob expansion and shell builtins will not work automatically. Put complex verification in a script, or explicitly use a shell such as `bash -lc '...'`. Relative command paths resolve inside the worktree. For a virtual environment living in the original repository, use the interpreter's absolute path: untracked environments are not copied to worktrees.

## Watch parallel agents work

From the GoalForge directory, after configuring `.env`:

```bash
python3 examples/run_parallel_demo.py
```

On Windows, use `python examples/run_parallel_demo.py`. No package installation is needed for this script. It creates a fresh demo repository next to GoalForge, commits the starting files, and uses your configured LLM. Each invocation gets a new directory, so you can run the demo repeatedly.

The goal has three independent modules: text slugification, numeric median, and leap-year calculation. Its 13 acceptance tests intentionally fail initially. The planner is asked to assign one module to each worker. A successful run looks roughly like this (tool calls and completion order vary):

```text
[planner] START planner
[critic] START critic
[orchestrator] Dispatching 3 coding worker(s); cap=3
[coder-1] START Implement slugify | files: text_tools.py
[coder-2] START Implement median | files: stats_tools.py
[coder-3] START Implement leap-year check | files: date_tools.py
[coder-2] tool: read_file
[coder-1] tool: read_file
[coder-3] tool: write_file
[coder-3] DONE (1 changed files)
[coder-1] DONE (1 changed files)
[coder-2] DONE (1 changed files)
[orchestrator] Integrating worker patches...
  check: ...
    PASS
[reviewer] START reviewer
  KEPT: ...
Status: complete
```

Coder HTTP requests and tool loops run concurrently in Python worker threads. Each has a separate conversation and Git worktree. They use the same configured model and API key. This is real concurrent execution, not a sequential simulation; provider-side queuing can still limit speed.

`--workers 3` sets a concurrency **ceiling**, not a requirement to create three tasks for every goal. The planner may select fewer useful tasks. For example, the tiny single-function bug below should usually use only one worker. The three-module demo makes parallel work natural. Use `--workers 1` to retain the original sequential coding mode, or increase the ceiling up to 8.

```bash
goalforge run "Implement independent features A and B" --repo /path/to/project --check "python -m unittest discover -s tests" --workers 2
goalforge resume RUN_ID --workers 3
```

The planner defines exact file ownership and shared interface requirements. The critic reviews whether tasks can really proceed independently. Workers read the shared plan but implement only their assigned task. There is **no direct peer chat or shared mutable working tree**: communication is through the plan, structured results, patches, and the orchestrator. If one task needs another task's output, the planner should schedule it in a later iteration.

Only disjoint file assignments are accepted. File tools enforce ownership; a worker patch touching an unassigned or protected file is rejected even if the change came from an approved command. These checks are workflow controls, not OS sandboxing.

After all workers finish, their patches are applied to the integration worktree in a deterministic order. The complete change must pass all required checks and the reviewer. If a worker or integration fails, that incomplete batch is discarded and any previous repair candidate survives. A completed combined implementation that fails verification or review is saved as an **unaccepted repair candidate** for targeted revision. Earlier accepted iterations stay intact. Individual worker results are not independently accepted.

LLM-suggested commands still ask for approval. Requests are queued and presented one at a time on the main thread; output labels identify the requesting worker. Add `--yes` only when you want unattended command execution. Ctrl-C cancels pending work; already-running HTTP requests or commands may need to finish or reach their timeout before cleanup completes.

`history RUN_ID` includes each worker's task, changed paths, summary and patch reference for successfully collected results. `events.jsonl` records worker start/finish/failure, role calls, approvals and integration with timestamps, agent IDs and attempt numbers. For detailed live inspection, use `tail -f /path/to/run/events.jsonl` in a second terminal. Use `goalforge serve` for the live graphical dashboard.

Generated parallel demos are created inside `demos/` in the GoalForge project. This directory is kept with `.gitkeep`; generated repositories are ignored by the parent Git repository. An explicit `--demo-dir` overrides the destination.

To create only the example repository without making API requests:

```bash
python3 examples/run_parallel_demo.py --prepare-only
```

## Try the small sequential example

`examples/tiny_project` has a deliberately failing test for an empty average. This example is separate from GoalForge's own passing tests.

From the GoalForge directory, with its environment activated:

```bash
cp -R examples/tiny_project ../goalforge-demo
git -C ../goalforge-demo init
git -C ../goalforge-demo add .
git -C ../goalforge-demo -c user.name=Demo -c user.email=demo@localhost commit -m "Initial example"

goalforge run "Make average([]) raise ValueError containing 'at least one'; preserve nonempty averages" \
  --repo ../goalforge-demo \
  --check "python -m unittest discover -s tests -v" \
  --protect tests
```

In PowerShell, replace the copy command with `Copy-Item examples/tiny_project ../goalforge-demo -Recurse`, and run the final command on one line. Configure your endpoint and model first; a real run makes billable requests where your provider charges for them.

## How it works

### Relationship to the paper

[RecEvolve: A Knowledge-Driven Autonomous Agent System for Recommender Systems](https://arxiv.org/abs/2609.01622), especially [Sections 3.2–3.4](https://arxiv.org/html/2609.01622v1), describes orchestrated, stateless roles, centralized knowledge, isolated execution, and evaluation-driven keep/rollback decisions. Its reported memory limitations and evaluation shortcuts motivate explicit failure records and protected checks here.

| Paper idea | GoalForge adaptation |
| --- | --- |
| Domain-knowledge directive | Your coding conventions and goal |
| Ideator and critic | Separate proposal and feasibility calls |
| Coding agent | Local file and command tools |
| Centralized knowledge | Durable attempt history injected into later roles |
| Isolated experiments | An integration worktree plus temporary worktrees for parallel coders |
| Evaluation and repair | Required checks plus review; accept verified code or retain an isolated repair candidate |

This adaptation replaces recommender-model training with ordinary software verification and stops at a finite goal. It uses a bounded pool of concurrent coding workers sharing one model, with sequential planning and final review. It does not include distributed training, literature search, or production deployment.

### Execution details

1. **Create a run.** Record the original commit, goal, directive, commands and protected paths. Create branch `goalforge/<run-id>` and its own worktree outside the repository.
2. **Measure the baseline.** Execute your checks and retain their results. Restore the committed source after the baseline so check-generated source edits do not become the starting point.
3. **Plan.** Give a fresh planner the goal, file index, baseline, and recent lessons. In parallel mode it proposes independent tasks with exact, nonoverlapping file assignments. In sequential mode it proposes one coherent increment.
4. **Critique.** A separate read-only role reviews the plan. A rejection becomes a lesson. An exact normalized proposal already tried on the same accepted commit is skipped.
5. **Implement.** Concurrent coders read and edit their assigned files in separate worktrees created from the last accepted commit. Each receives the common plan and its own task. File edits happen automatically; model commands ask for approval unless `--yes` is set. With `--workers 1`, a single coder edits the integration worktree directly.
6. **Integrate, verify and review.** Wait for every worker, validate ownership, and apply all worker patches to the integration worktree. Execute your fixed checks. Stage the diff, reject changes to protected/private paths, and ask a fresh reviewer to assess the implementation and evidence. Diffs over 60,000 characters are rejected so the agent must split large changes.
7. **Accept or repair.** Only passing checks plus reviewer acceptance promote a change to the accepted branch. Completed failed candidates are checkpointed separately and restored for the next repair attempt. At rest the run worktree returns to the accepted commit; unaccepted candidate code remains reachable through private Git refs and saved state.
8. **Record and continue.** Save the outcome and lesson. Stop when the reviewer marks the full goal complete, the attempt/request limit is reached, an error occurs, or you interrupt execution.

“Complete” means the configured checks passed and the LLM reviewer judged the goal satisfied. It is not a formal correctness guarantee. Choose checks that exercise the behavior you actually want. A reviewer can miss bugs or accept superficial changes; review the resulting branch before integrating it.

Each role starts with fresh context. Tools preserve a bounded conversation within that role; older tool exchanges are dropped when it grows too large. The newest 12 attempt summaries are supplied to later roles. The full log stays on disk. Memory is local to a run, not a global learning system; exact duplicate detection does not identify semantically equivalent proposals.

## Review, resume, and integrate

```bash
goalforge list
goalforge status RUN_ID
goalforge history RUN_ID
goalforge resume RUN_ID --iterations 5 --max-calls 80
```

Resume preserves the goal, checks, accepted commits, and history. It reruns an interrupted attempt from the last accepted commit; it does not resume an individual tool conversation. **Unaccepted manual edits in the agent worktree are discarded on resume.** Make your own edits on another branch or commit/integrate them separately. Do not run two sessions against the same run; an OS lock prevents concurrent execution.

The worker limit is saved for resume; old runs without a worker setting keep sequential mode unless you pass `--workers`. Provider/model configuration is saved for resume; keys are never saved in run state. `--model` and `--base-url` (or environment / `.env` settings) can override the saved values for an invocation. Each resume gets a new request/iteration budget. Lifetime usage counters are shown separately. Reported token usage depends on the provider supplying usage fields; there is no dollar-cost cap.

From the original repository, inspect and integrate the branch printed by the CLI:

```bash
git diff ORIGINAL_COMMIT..goalforge/RUN_ID
git log --oneline ORIGINAL_COMMIT..goalforge/RUN_ID
git merge --ff-only goalforge/RUN_ID
```

Worker worktrees are removed on success, failure or cancellation. Resume also cleans up recorded worker worktrees left by a process crash before restarting from the last accepted commit.

If your original branch has advanced, fast-forward merging may fail. Review and merge or cherry-pick using your normal Git workflow. GoalForge never automatically merges, pushes or deploys.

Exit codes: `0` for complete or successful inspection, `2` for an incomplete/budget-limited or paused run, and `1` for errors. An interrupt before the engine starts returns `130`.

## Budgets and storage

| Option | Default | Meaning |
| --- | --- | --- |
| `--workers` | 3 for new CLI runs | Concurrent coding workers (1–8); planner may use fewer |
| `--iterations` | 5 | Attempts during this invocation |
| `--max-calls` | 80 | Shared across all roles and workers, including retries |
| `--role-steps` | 30 | Responses/tool steps per non-coding role |
| `--coder-steps` | 30 | Responses/tool steps per coding agent, including parallel workers |
| `--max-tokens` | 4096 | Requested output-token limit per response |
| `--timeout` | 120 | Seconds per local command |
| `--api-timeout` | 90 | Seconds per HTTP request |
| `--yes` | off | Authorize model commands without prompts |
| `--runs-dir` | `~/.goalforge/runs` | State/worktree location outside the target repo |

`GOALFORGE_HOME` changes the parent directory of `runs`. Use the same `--runs-dir` on subsequent inspection/resume commands if you customize it. There is no automatic background scheduler. More workers may spend the shared request budget faster; the limit is not multiplied per worker.

Each run contains:

```text
RUN_ID/
  state.json       # goal, configuration, checkpoints, history, usage
  events.jsonl     # role replies, tool results, checks and decisions
  run.lock         # OS-lock handle; harmless when no process holds it
  workspace/       # Integration worktree on the goal branch
  workers/
    attempt-1/
      coder-1.patch  # Worker patch retained for inspection
      coder-2.patch  # Temporary worker worktrees are removed after the attempt
```

The key is redacted from recorded tool results. Logs still contain source code and command output, so treat them as private project data. Removing a run's storage prevents resume. After integrating its changes, remove its worktree with `git worktree remove PATH` before deleting the run directory; Git should manage worktree registration.

## Execution boundaries and limitations

**A Git worktree is not an OS sandbox.** Approved commands and your verification commands execute with your user's permissions. They can access the network and files outside the worktree. For untrusted projects or unattended `--yes` use, run the entire tool inside a container or disposable VM with only the intended files mounted.

Built-in file tools reject path traversal, symlinks, Git metadata, common credential filenames and configured protected paths. Command execution is broader: it can bypass file-tool restrictions, modify Git state, or produce side effects a rollback cannot undo. GoalForge checks the resulting diff for protected files, but this is not tamper-proof evaluation isolation. Do not expose credentials in the target repository. The LLM key and common secret environment variables are removed from child-process environments; this is not a comprehensive secret-discovery system.

Other deliberate constraints:

- Rejected attempts remove untracked nonignored files only. Ignored caches, virtual environments and build outputs survive rollback; checks should be repeatable and clean their own build output if necessary.
- Only committed source is copied into the worktree. Dependencies, ignored assets, submodules and external services may need setup.
- Commands time out; POSIX process groups and Windows process-tree termination are used where available. Processes deliberately escaping that group/tree are not contained. Temporary output files bound Python memory usage, not disk usage.
- No arbitrary binary editing, browser automation, web research, streaming UI, or model routing is included.
- Completion quality depends on the model, task size, context, and checks. Large goals usually need multiple accepted increments or narrower goals.
- The API adapter targets a common protocol, not every provider's extensions. Retries for rate limiting/transient server failures consume the request budget.

## Response format troubleshooting

OpenAI agents use native file/command functions and a structured `finish` function. Other providers are instructed to return either a JSON tool request or `{"final": {...}}` with their role's result. GoalForge also accepts unwrapped final fields when they satisfy the same schema. Missing fields, string values in place of booleans, ambiguous tool/final combinations, and invalid file ownership remain errors.

A coding role cannot finish before calling a workspace tool. A summary claiming that no tools are available triggers corrective feedback rather than silently counting as completed coding.

Format rejections are printed in the terminal and saved as `response_rejected` events with a concrete reason. Three consecutive invalid responses stop that role early to avoid spending the entire step budget on the same formatting problem. Accepted unwrapped results are logged as `response_normalized`. You can resume a stopped run after fixing its cause; increasing the request budget alone will not fix a repeated format error.

## Development and tests

```bash
python -m unittest discover -s tests -v
```

Tests use temporary Git repositories and a local mock HTTP server, so no key or paid request is needed. They exercise the CLI-to-HTTP workflow, barrier-proven concurrent workers, atomic integration/rollback, crash recovery, cancellation during approval, shared request budgets, ownership checks, memory, locking, file boundaries, timeout handling and credential handling. Live provider behavior and real-model coding quality require separate testing with your endpoint.

```text
src/goalforge/
  cli.py          # commands, settings and terminal output
  config.py       # literal .env loading and environment precedence
  provider.py     # HTTP adapter, native/text response parsing, request budgets
  tool_schemas.py # role-specific native function schemas
  engine.py       # role prompts and orchestration
  swarm.py        # concurrent coders, ownership validation and patch integration
  workspace.py    # file tools and subprocess execution
  state.py        # durable state, locking and Git checkpoints
examples/
  directive.md
  tiny_project/   # single-function bug
  parallel_project/ # three independent modules and protected acceptance tests
  run_parallel_demo.py # one-command live parallel demo
```

To extend it, add a provider adapter implementing `complete(messages, tools=...) -> dict`, `fork()` for a worker client, and a shared thread-safe `budget` object, add tools in `Workspace.execute`, or customize the role prompts and acceptance policy in `engine.py`.

MIT licensed. The paper is credited as architectural inspiration; no paper code or production artifacts are bundled.

## Guided demo preparation

From the project folder, run:

```sh
.venv/bin/python examples/prepare_demo.py
```

The wizard lists scenarios with descriptions, lets you choose one, asks for a new
folder name, and sets the suggested coding worker count. Everything is prepared
under `demos/<name>/` as a separate, clean Git repository. Existing directories
are never overwritten. Preparation is offline and makes no model requests.

- **utilities**: the original three independent functions, for a quick parallel run.
- **taskboard**: server-rendered HTML frontend, request handler and SQLite storage.
  Workers must agree on adapter methods, task shapes, validation and escaping.
- **reservations**: inventory frontend, API and transactional SQLite storage.
  Exercises durable idempotency, concurrent reservations, rollback and errors
  crossing layer boundaries.

```sh
# List choices, or prepare without interactive prompts:
.venv/bin/python examples/prepare_demo.py --list
.venv/bin/python examples/prepare_demo.py --demo reservations --name stock-loop --workers 3
```

Each folder contains `DEMO.md` with a ready-to-run CLI command and the goal,
check command, protected paths, worker count and budgets to enter in the web UI.
`demo.json` stores those settings for reference; the UI does not import it automatically.
The larger demos use a framework-independent request handler and server-rendered
HTML functions, so they need no Node tooling, web framework or database service.
They are coding exercises, not independently hosted browser applications.

Baseline acceptance checks intentionally fail. The planner must define the shared
internal contract before dispatch; workers implement separate modules against it.
The reviewer evaluates the combined implementation. Failed candidates and feedback
feed targeted repair attempts; accepted incomplete work continues from its accepted
checkpoint. Multiple loops are possible, not guaranteed: a correct implementation
can finish on its first attempt. The reservation demo is the harder stress test.
The existing `examples/run_parallel_demo.py` command remains available.

## Inspect agent input context

For new runs, click an agent node and open **Context supplied to this agent** in
the sidebar. Select a model request, then expand the scrollable cards for the goal,
system instructions, planner proposal/shared interfaces, worker assignment,
previous-loop lessons, baseline results, review diff, check results or conversation
messages. Each card names its source; the view labels the run, loop, request and
workspace. Click a historical **Context supplied** activity event to inspect an
older loop. This shows supplied inputs and explicit agent outputs, not hidden reasoning.

Snapshots are `agent_context` records in the run's `events.jsonl`, captured immediately
before each model call with the configured API secret redacted. They reflect context
pruning and truncated tool output as actually supplied by the engine, and include
native tool definitions when enabled. Provider-side processing is not recorded.
Snapshots repeat conversation context, so logs can grow substantially and contain
repository source and command output; treat them as local project data. Older runs
have no snapshots and are labeled accordingly. Restart your local GoalForge server
and refresh the page after updating to load this feature.


### Browsing attempts and recovering a stalled plan

Use the attempt selector above the graph, or Previous / Next, to inspect any loop
while the current one continues. Selecting an attempt pins its graph, activity and
agent inputs; **Follow latest attempt** resumes automatic tracking. Each completed
attempt shows its outcome and feedback. Baseline checks have their own entry.

Older runs without exact input snapshots show recovered planner assignments and
recorded tool results, with explicit provenance and an explanation of what is
unavailable. These records are not claimed to be the full original model prompt.
If the UI detects an older server process, it displays a restart notice; a browser
refresh alone does not reload Python code.

The critic approves an implementation **plan**, while the reviewer evaluates the
**actual implementation** after coding. Placeholders and failing baseline tests
are not by themselves grounds for rejecting a plan. Parallel plans must specify
exact shared symbols, method signatures, return shapes and error behavior before
workers start. After two consecutive plan rejections on the same checkpoint,
GoalForge switches to a single-worker implementation trial with advisory critic
feedback (see disagreement recovery below). Verification and final review still
determine whether its changes can be accepted.

If verification commands are blank (or CLI `--check` is omitted), GoalForge defaults to
`<its Python executable> -m unittest discover -s tests -v` in the run workspace.
Explicit commands replace this default; resumed runs retain their saved checks.

### Context panel controls

Click **Expand panel** to use the full page; **Restore sidebar** or Escape returns
it to its normal width. Context is split into **Initial inputs** (including any
planner or prior-attempt feedback supplied when this agent started) and
**Accumulated during this agent's work** (its messages, tool results and corrections).
The search box searches card titles, sources and contents, including collapsed
cards, for the selected request. Matching cards expand automatically. Search text
and the selected request are retained when the panel updates.

### API throttling and retries

The client shares a cooldown across parallel workers in the same run. Temporary
HTTP 429 and server-overload responses use `Retry-After` timing when supplied
(seconds, HTTP date, or `retry-after-ms`); exhausted request/token reset headers
are a fallback. Otherwise it uses exponential backoff with jitter. Recovery
requests are spaced at least half a second apart after a cooldown. Already
in-flight requests cannot be recalled. Cooldowns are local to this run, not
coordinated with other applications using the same API account.

Each model call allows up to six retries, with a five-minute cooldown/retry window;
a longer server delay stops automatic retries instead of retrying early. Every
HTTP attempt counts against the shared request budget. Waiting is cancellable
with Pause. Retry and wait events appear in the activity list and terminal.
Persistent temporary rate limits pause the run for later resumption; known
billing/quota errors stop immediately with an actionable message. No raw provider
error bodies are logged. An individual in-flight HTTP request still has its own
request timeout. This follows the [OpenAI rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits).

### File-by-file changes and review failures

Use the left/right arrows in the context panel to step through model requests.
The position label shows the request number and total for the selected agent/loop.

**Final changes** opens a file list with addition/deletion counts and a unified,
line-numbered diff for the selected file. This compares the starting commit to the
latest accepted commit; unaccepted work is excluded. Expand the panel to put the
file list beside the diff. Worker diffs use the same viewer.

Select an attempt and click **View attempt changes & review**, or click the
reviewer node and **Inspect attempt changes & failures**. The viewer shows the
reviewer's reason, repair guidance, and verification results, with failed command
output expanded. Files explicitly mentioned in feedback get a Review label;
these are filename references, not inferred line-level findings.

New combined candidates and check results are saved under
`~/.goalforge/runs/<run-id>/attempts/<attempt>.json` before review or rollback.
Rejected edits therefore remain available for inspection even though the working
files are restored. Older attempts use recorded reviewer input or accepted Git
commits where available. Attempts that never reached integration, or old runs
without these records, explicitly report that a combined diff is unavailable.


## Targeted repair loop

GoalForge keeps two separate checkpoints: **accepted code** and, when needed, an
**unaccepted repair candidate**. After coding and verification, a completed candidate
is saved before the reviewer call. Failing tests or a rejected review no longer
throw away that implementation. The next planner sees its actual files, latest
check output (last 12,000 characters per check), worker summary and reviewer feedback,
and assigns the smallest coherent repair. Coupled API/storage fixes can use one
worker owning both files; independent repairs can still run in parallel.

All coding workers start from the same candidate checkpoint. The reviewer sees the
cumulative diff against accepted code, while the attempt diff shows only that
attempt's edits. Every promotion still requires all configured checks and reviewer
acceptance. Candidate commits never become ancestors of the accepted commit:
promotion commits the verified combined tree onto the previous accepted checkpoint.
Your original checkout is changed only through explicit integration.

Candidates survive budget exhaustion, API failures during review, and process
restarts. Interrupted individual workers or incomplete integration batches are
not promoted to candidates; resume uses the last completed saved candidate.
At rest, the integration worktree and public run branch point at accepted code.
Use **Repair candidate** in the UI to inspect pending work, and Resume to continue
repairing it. After three candidate rounds, planning is instructed to reconsider
architecture/ownership while preserving useful code. Three identical consecutive
candidate trees pause the run for guidance instead of endlessly repeating repairs.
Existing request, role-step, attempt and planning-stall limits still apply.

To intentionally abandon pending work, use the **Discard saved repair candidate**
checkbox when resuming, or:

```sh
goalforge resume RUN_ID --discard-candidate
```

The state pointer is cleared; historical candidate refs remain for inspection.
Candidates live in `refs/goalforge/candidates/<run-id>/attempt-<number>` and are
tracked in `state.json`. Old runs remain readable; previously discarded code is
not automatically reconstructed from incomplete or redacted historical logs.
Restart the server after updating the repair engine.


### Scope-aware planning and disagreement recovery

Plans must state CURRENT INCREMENT, DEFERRED WORK, SHARED INTERFACES for the workers
being dispatched now, and OBJECTION RESPONSES addressing prior critic feedback.
The critic judges the increment, not whether every future feature has already
been designed. It may block a deferred requirement only when there is a concrete
compatibility or dependency problem with the current work. Tests require assigned
files, not a dedicated testing agent. Full-goal completion remains the reviewer's
responsibility, and configured verification is never weakened.

The planner receives recent rejected-plan objections explicitly. After two
consecutive rejections on the same checkpoint, the next plan is limited to one
coherent task owning the coupled implementation/test files. This is enforced in
the model tool schema and plan validation, applies across resumes, and is visible
as a planning fallback event. Private interfaces within that worker's assignment
can be chosen during implementation. The fallback uses only the current plan's
single assignment. It does not import file ownership or instructions from older
rejected plans, which may describe features the current plan deliberately deferred.
After these two vetoes, critic feedback becomes advisory for the single-worker
implementation trial and is passed to the coder. The critic's rejection is still
logged, together with a `planning_trial` event explaining the dispatch decision.
This deliberately allows a potentially flawed plan to be tested in the isolated
workspace instead of repeatedly debating it. File protections, verification and
final reviewer approval still gate acceptance; a failed trial becomes a repair
candidate. Duplicate-plan detection does not block this trial. Other planning
failures can still pause the run.
These rules improve recovery but cannot guarantee model agreement or completion.

### Repair versus advancement

Each attempt receives an explicit `progress` context derived from the latest
candidate checks and review, or the last accepted increment. Failed checks and
concrete implementation defects select **repair**. Passing checks with no recorded
defects select **advance**: the next plan must implement an unmet requirement,
rather than preserve already-working behavior. Older saved candidates without
structured reviews select **reassess**, so stale failures are not assumed to persist.

Reviewers separately report `defects`, `remaining_work`, and `next_increment`.
A sound partial increment is accepted with `complete=false`; unfinished future
features alone must not cause rejection. Structured contradictory answers are
returned to the reviewer for correction. Passing tests never override a reported
implementation defect, and reviewer approval never overrides failing checks.
Older reply formats remain readable; new native reviewer calls require these fields.

The Decisions panel exposes the next-step context, concrete defects, remaining
requirements and next increment. Attempts with no file changes are explicitly
marked, and existing stagnation limits still apply. These checks make the workflow
clearer but cannot prove that a model's design or review is correct.

When a configured unittest discovery command uses `-s tests`, GoalForge also runs
it with `-s tests_extra` if that directory contains test files, retaining the same
interpreter and other flags. Extra-test failures block acceptance. Custom commands
are unchanged; configure those to cover all relevant suites yourself.

### Active context and historical evidence

Agents start with a compact account of the loaded checkpoint: the goal and user
constraints, current check statuses (with short failure excerpts only when checks
fail), the latest accepted capability, current defects/remaining work, and the
current assignment. The original failing baseline and old plans are no longer
copied into every prompt. An accepted legacy checkpoint without saved check output
is marked for reassessment; its original baseline is not treated as current evidence.
Only unresolved planning blockers and the latest execution/repair lesson are carried
forward automatically. Interrupted attempts retain their proposal and no longer
reset the repeated-veto fallback counter.

Every role can call the read-only `retrieve_history` tool when more evidence is
needed. Supply an earlier attempt number, a section (`plan`, `review`, `checks`,
`workers`, `outcome`, or `events`), and a character page (`start`, `count`, maximum
12,000). Attempt `0`, section `checks`, retrieves the original baseline. The result
includes `next_start` for pagination. Retrieved excerpts enter the conversation as
tool results and are logged; they do not silently become current facts. Existing
log/output truncation still applies: retrieval cannot recover data never recorded.

In the agent inspector, **Active context · initial inputs** and **Active context ·
accumulated during this agent’s work** show the exact selected request. **Archive ·
not automatically supplied** retains earlier decisions, recorded traces, baseline
results, and messages pruned from this invocation. Expand these records or search
their contents without feeding them to the agent. An archived record can overlap
with an excerpt explicitly retrieved into active context; the active message list
is authoritative. Older runs retain their original input snapshots rather than
being retroactively rewritten.

### Evidence for reopening work and progress gates

Planner results declare `increment_kind`, `observable_change`, and reopening
fields. In advance mode, a proposed repair must explain the violated requirement
and reference either a currently failing check, exact source text in an existing
workspace file, or an explicit user instruction. Historical baseline failures are
not sufficient. This verifies the existence of the cited evidence, not whether the
model's interpretation is correct; the critic, checks, and reviewer still matter.

New native critic results classify blockers and supply evidence. Missing destination
files/directories are not valid blockers: workers can create them. A single worker
owning code and tests cannot be rejected for a cross-worker interface dependency.
The critic must assess the actual increment, not require implementation of deferred
features or demand that new tests already exist. Invalid structured decisions receive
bounded correction feedback before dispatch.

Before candidate promotion/review, the harness compares the attempt to its starting
checkpoint. Unchanged files and trailing-newline-only changes do not count as an
increment. Ordinary Python formatting/comment-only edits are detected using equal
syntax trees; changes to strings, indentation that alters the tree, or tool directives
are not dismissed as cosmetic. These are conservative checks, not a universal proof
of semantic equivalence for every file type. Two consecutive `no_progress` attempts
pause the run, preserving the prior candidate and accepted code. The reviewer also
receives the attempt-specific diff, so cumulative earlier work cannot masquerade as
new progress. A larger request budget does not bypass these gates.

### Which limit stopped a run?

The web UI and CLI distinguish three resumable stops:

- **Attempt limit reached:** the configured number of attempts for this start/resume
  session completed without finishing the goal. Increase Attempts when resuming.
- **Shared request limit reached:** all agents together used the session's HTTP
  request allowance, including retries. Increase Request budget when resuming.
- **Agent step limit reached:** a particular agent exhausted its model/tool loop
  allowance. The message names the agent and role and gives its step limit. Narrow
  the task or increase `--coder-steps` (coders) / `--role-steps` (other roles).

Run state retains `status: budget_exhausted` for compatibility and adds a structured
`stop_reason` with `kind`, `limit`, `used`, agent identity where applicable, and an
explanation. `limit_reached` events preserve these stops in the trace. Resuming
clears the previous stop reason. Old runs with an explicit recorded error can be
labeled; otherwise the UI says the limit type was not recorded rather than guessing
from cumulative usage. Rate-limit (HTTP 429) pauses remain separate from these limits.

### Diagnose the test as well as the implementation

A failed assertion establishes a mismatch, not its cause. Planning and review now
include structured `failure_analysis`: observed result, classification
(`implementation`, `test_expectation`, `environment`, or `uncertain`), relevant
files, requirement, independently derived expected result, evidence, and next action.
Classifying an implementation or expectation defect requires reading both the test
and implementation during that role invocation. Repeated failing check/test identities
are highlighted in `failure_diagnosis`; agents must inspect intermediate state instead
of repeating a speculative fix. These fields are visible in Plans & decisions and
the exact active context. Historical reviewer explanations remain hypotheses until
supported by evidence.

For example, a fixture with 5 available items, an idempotent reservation replay,
and a new order consuming 1 item should have 4 available after reopening. A generated
assertion expecting 3 is a test defect; migration must not consume an extra item just
to satisfy it.

Test provenance comes from the run's original Git commit. Existing original Python
test files are protected automatically in addition to configured protected paths.
New tests remain subject to review. To correct an existing agent-generated test,
the planner must read the test and implementation and propose `test_corrections`
with an exact old/new assertion, requirement, expected-value derivation, evidence,
and explanation of preserved coverage. Supported corrections retain the equality
assertion and actual expression, changing only the concrete expected value.
Deleting/skipping tests, tautologies, fixture changes, and edits beyond the exact
proposed replacement are rejected. Add new regression scenarios in new test files;
this narrow correction mechanism does not authorize rewriting existing test suites.

The reviewer must independently read and assess a correction, explicitly record
`test_corrections_valid` and `test_correction_review`, and all verification commands
must still pass. Protected original tests cannot be corrected through this mechanism;
an apparent conflict must be reported rather than silently weakened. Evidence checks
and edit boundaries are enforced in code, but interpretation of a requirement still
requires model/human judgment.

Verification commands receive a fresh Python bytecode-cache location so same-size
edits within one timestamp tick cannot accidentally execute an older cached assertion.
