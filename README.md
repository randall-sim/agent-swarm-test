# GoalForge

A small Python CLI coding agent inspired by **RecEvolve**. Give it a coding goal, a repository, verification commands, and an LLM API key. It inspects code, proposes a change, critiques the plan, edits files, runs checks, and reviews the result. Useful changes accumulate on a separate Git branch.

Use it for bug fixes, small features, refactoring, tests, and documentation. It is a working starter implementation, not a reproduction of the paper's production infrastructure or a claim of equivalent results.

- Python 3.10+ and Git; **no third-party Python runtime dependencies**.
- One process, with sequential planner, critic, coder, and reviewer LLM calls.
- Configurable Chat Completions-compatible endpoint and model.
- Persistent run history, isolated Git worktree, bounded requests and attempts.
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

## Configure your LLM

Use your provider's **API base URL**, typically ending in `/v1`, and its exact model ID. The tool appends `/chat/completions`. An API key alone does not identify your provider or model.

macOS/Linux/WSL:

```bash
export LLM_BASE_URL="https://YOUR-PROVIDER/v1"
export LLM_MODEL="YOUR-MODEL-ID"
# Optional: set LLM_API_KEY in your environment.
# If omitted, GoalForge asks for it privately when you start a run.
```

PowerShell:

```powershell
$env:LLM_BASE_URL = "https://YOUR-PROVIDER/v1"
$env:LLM_MODEL = "YOUR-MODEL-ID"
# Optional: set $env:LLM_API_KEY, or use the hidden prompt.
```

The key is not saved in run configuration. Prefer the hidden prompt over putting a real key into shell history. For automation, supply `LLM_API_KEY` through your environment or secret manager. `--key-env NAME` reads another variable instead. Noninteractive runs require that variable to be set.

The client sends `model`, `messages`, and `max_tokens`, and expects text containing JSON in `choices[0].message.content`. It does not require native function calling or JSON-schema support. Select a model that follows structured instructions. Native Anthropic Messages, Gemini, and other incompatible API shapes require a compatible gateway or an adapter in `provider.py`. Providers that require different token parameters also need an adapter. No automatic provider detection is attempted.

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

## Try the included example

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
| Isolated experiments | One Git worktree per goal |
| Evaluation and rollback | Required checks plus review, then commit or revert |

This adaptation replaces recommender-model training with ordinary software verification and stops at a finite goal. It uses sequential roles sharing one model, without distributed training, parallel experiments, literature search, or production deployment.

### Execution details

1. **Create a run.** Record the original commit, goal, directive, commands and protected paths. Create branch `goalforge/<run-id>` and its own worktree outside the repository.
2. **Measure the baseline.** Execute your checks and retain their results. Restore the committed source after the baseline so check-generated source edits do not become the starting point.
3. **Plan.** Give a fresh planner the goal, file index, baseline, and recent lessons. It can read files and search text before proposing one coherent increment.
4. **Critique.** A separate read-only role reviews the plan. A rejection becomes a lesson. An exact normalized title/approach already tried on the same accepted commit is skipped.
5. **Implement.** The coder reads and edits files and may request commands. File changes happen automatically in the dedicated worktree; model commands ask for approval unless `--yes` is set.
6. **Verify and review.** Execute your fixed checks. Stage the diff, reject changes to protected/private paths, and ask a fresh reviewer to assess the implementation and evidence. Diffs over 60,000 characters are rejected so the agent must split large changes.
7. **Keep or revert.** Only passing checks plus reviewer acceptance can keep a change. An accepted partial increment is committed and becomes the next starting point. A rejected attempt restores the last accepted commit and deletes untracked, nonignored files in the agent worktree.
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

Provider/model configuration is saved for resume; keys are never saved. `--model` and `--base-url` (or environment variables) can override the saved values for an invocation. Each resume gets a new request/iteration budget. Lifetime usage counters are shown separately. Reported token usage depends on the provider supplying usage fields; there is no dollar-cost cap.

From the original repository, inspect and integrate the branch printed by the CLI:

```bash
git diff ORIGINAL_COMMIT..goalforge/RUN_ID
git log --oneline ORIGINAL_COMMIT..goalforge/RUN_ID
git merge --ff-only goalforge/RUN_ID
```

If your original branch has advanced, fast-forward merging may fail. Review and merge or cherry-pick using your normal Git workflow. GoalForge never automatically merges, pushes or deploys.

Exit codes: `0` for complete or successful inspection, `2` for an incomplete/budget-limited or paused run, and `1` for errors. An interrupt before the engine starts returns `130`.

## Budgets and storage

| Option | Default | Meaning |
| --- | --- | --- |
| `--iterations` | 5 | Attempts during this invocation |
| `--max-calls` | 80 | HTTP requests, including retries |
| `--role-steps` | 12 | Responses/tool steps per role |
| `--max-tokens` | 4096 | Requested output-token limit per response |
| `--timeout` | 120 | Seconds per local command |
| `--api-timeout` | 90 | Seconds per HTTP request |
| `--yes` | off | Authorize model commands without prompts |
| `--runs-dir` | `~/.goalforge/runs` | State/worktree location outside the target repo |

`GOALFORGE_HOME` changes the parent directory of `runs`. Use the same `--runs-dir` on subsequent inspection/resume commands if you customize it. There is no automatic background scheduler.

Each run contains:

```text
RUN_ID/
  state.json       # goal, configuration, checkpoints, history, usage
  events.jsonl     # role replies, tool results, checks and decisions
  run.lock         # OS-lock handle; harmless when no process holds it
  workspace/       # Git worktree on the goal branch
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

## Development and tests

```bash
python -m unittest discover -s tests -v
```

Tests use temporary Git repositories and a local mock HTTP server, so no key or paid request is needed. They exercise the CLI-to-HTTP workflow, rollback, resume, check gating, request budgets, memory, locking, file boundaries, timeout handling and credential handling. Live provider behavior and real-model coding quality require separate testing with your endpoint.

```text
src/goalforge/
  cli.py          # commands, settings and terminal output
  provider.py     # HTTP adapter, response parsing, request budgets
  engine.py       # role prompts and orchestration
  workspace.py    # file tools and subprocess execution
  state.py        # durable state, locking and Git checkpoints
examples/
  directive.md
  tiny_project/   # intentionally failing target for a first goal
```

To extend it, add a provider adapter implementing `complete(messages) -> dict` and a `budget` object, add tools in `Workspace.execute`, or customize the role prompts and acceptance policy in `engine.py`.

MIT licensed. The paper is credited as architectural inspiration; no paper code or production artifacts are bundled.
