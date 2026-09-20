# Task board contract workshop

Build a server-rendered task board with a SQLite persistence adapter. No external dependencies.
Public entry points (keep these signatures):
- `storage.Store(path)`; `close()` releases its SQLite connection.
- `api.handle(store, method, path, body=None)` returns `(HTTP_status, JSON_object)`.
- `frontend.render_board(tasks)` returns an HTML string.

Required routes:
- POST /tasks accepts title; 201 returns {task: {id: integer, title: trimmed string, done: boolean}}.
- GET /tasks returns 200 {tasks: [...]}, ordered by ascending id.
- PATCH /tasks/<id> accepts a boolean done; 200 returns {task: ...}.
- Bad title (blank, non-string, >120 characters) or non-boolean done -> 400 {error: string}.
- Missing task or unknown route -> 404 {error: string}.
- Unexpected database errors must not be reported as successful writes.

Frontend: show titles, data-task-id attributes and done/open state; escape untrusted titles.
Show an empty-state message containing 'No tasks'. Persistence must survive reopening the store.

Planner: BEFORE dispatch, specify the storage method names, arguments, return shapes,
error semantics and UI task shape in proposal.approach. Those internal interfaces are
intentionally undecided: choose and document them here. Use independent file ownership
for storage.py, api.py and frontend.py; assign CONTRACT.md to one worker.
Reviewer: compare every layer with the agreed contract and record concrete repair advice
for the next loop. Do not mark complete based only on passing tests; inspect validation,
escaping, error handling and resource lifetime. Add further implementation tests outside tests/.
