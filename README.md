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

Or, without activating the environment, run `.venv/bin/python -m goalforge serve /path/to/your/project` from the GoalForge directory. The server opens your browser and prints its local URL. Use `--no-open` to copy the URL yourself, or `--port 8766` if the default port is busy. In WSL, paste the printed URL into your Windows browser if automatic opening is unavailable.

The server reads `.env` from the directory where you launch it. To launch from elsewhere, pass `--env-file /path/to/goalforge/.env`. Your API key stays on the Python server; the browser receives only a flag indicating whether it is configured. Restart the server after editing `.env`.

The UI provides:

- **New goal:** choose a local folder using Browse, enter instructions and required verification commands, select the model, and set the worker count, request budget, and attempt limit. Commands use argument syntax, not shell pipelines. Use an absolute Python executable path when the target project needs its own virtual environment.
- **Live activity:** updates every second from the persisted event log. Agent cards show current activity; select an agent to filter the timeline. Click an action to see its tool arguments and result, task assignment, or stated decision. These are observable actions and explicit summaries, not private model reasoning.
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

`--check` is required and repeatable. All configured checks must exit zero, without timing out, before a candidate can be kept. Checks run automatically because you explicitly supplied them. A failing baseline is allowed so the agent can fix existing failures; every accepted candidate must pass all checks.

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

After all workers finish, their patches are applied to the integration worktree in a deterministic order. The complete change must pass all required checks and the reviewer. If a worker fails, integration fails, or combined verification fails, the **whole attempt** is reverted. Earlier accepted iterations stay intact. Individual worker results are not independently accepted or committed.

LLM-suggested commands still ask for approval. Requests are queued and presented one at a time on the main thread; output labels identify the requesting worker. Add `--yes` only when you want unattended command execution. Ctrl-C cancels pending work; already-running HTTP requests or commands may need to finish or reach their timeout before cleanup completes.

`history RUN_ID` includes each worker's task, changed paths, summary and patch reference for successfully collected results. `events.jsonl` records worker start/finish/failure, role calls, approvals and integration with timestamps, agent IDs and attempt numbers. For detailed live inspection, use `tail -f /path/to/run/events.jsonl` in a second terminal. There is no graphical dashboard yet.

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
| Evaluation and rollback | Required checks plus review, then commit or revert |

This adaptation replaces recommender-model training with ordinary software verification and stops at a finite goal. It uses a bounded pool of concurrent coding workers sharing one model, with sequential planning and final review. It does not include distributed training, literature search, or production deployment.

### Execution details

1. **Create a run.** Record the original commit, goal, directive, commands and protected paths. Create branch `goalforge/<run-id>` and its own worktree outside the repository.
2. **Measure the baseline.** Execute your checks and retain their results. Restore the committed source after the baseline so check-generated source edits do not become the starting point.
3. **Plan.** Give a fresh planner the goal, file index, baseline, and recent lessons. In parallel mode it proposes independent tasks with exact, nonoverlapping file assignments. In sequential mode it proposes one coherent increment.
4. **Critique.** A separate read-only role reviews the plan. A rejection becomes a lesson. An exact normalized proposal already tried on the same accepted commit is skipped.
5. **Implement.** Concurrent coders read and edit their assigned files in separate worktrees created from the last accepted commit. Each receives the common plan and its own task. File edits happen automatically; model commands ask for approval unless `--yes` is set. With `--workers 1`, a single coder edits the integration worktree directly.
6. **Integrate, verify and review.** Wait for every worker, validate ownership, and apply all worker patches to the integration worktree. Execute your fixed checks. Stage the diff, reject changes to protected/private paths, and ask a fresh reviewer to assess the implementation and evidence. Diffs over 60,000 characters are rejected so the agent must split large changes.
7. **Keep or revert.** Only passing checks plus reviewer acceptance can keep a change. An accepted batch or partial increment is committed and becomes the next starting point. A rejected attempt restores the last accepted commit and deletes untracked, nonignored files in the agent worktree.
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
| `--role-steps` | 12 | Responses/tool steps per role |
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
