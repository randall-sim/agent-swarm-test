# Inventory reservation contract workshop

Implement an inventory service, SQLite adapter and server-rendered stock view, using stdlib.
Public entry points:
- storage.Store(path), seed(sku, quantity), close(). seed is fixture/admin setup.
- api.handle(store, method, path, body=None) -> (status, JSON object).
- frontend.render_inventory(items) -> HTML.

GET /inventory -> 200 {items: [{sku: string, available: integer}, ...]} sorted by sku.
POST /reservations accepts {sku, quantity, request_id}, returns 201
{reservation: {id: integer, sku: string, quantity: integer, request_id: string}}.
Repeating an identical request_id and payload returns 200 with the SAME reservation,
without another stock decrement, including after the database is reopened.
Reusing request_id with a different sku or quantity -> 409 {error: string}.
Insufficient stock -> 409; missing SKU -> 404. All errors return {error: string}.
Quantity must be a positive integer (booleans invalid); request_id must be a nonblank string:
invalid input -> 400. Failed operations must not consume stock or reserve request_id.
Reservations and stock decrement must be one SQLite transaction, safe across connections.
Frontend must escape sku, display quantity and mark zero stock as 'Sold out'.

Planner must define shared adapter method signatures, row shapes, domain error semantics,
transaction boundary, and UI data contract in proposal.approach BEFORE parallel dispatch.
Storage, API and view workers own separate modules; one worker also records the agreed
internal interfaces here. Do not assume a worker can see another worker's unmerged files.
Reviewer must inspect retry/idempotency, rollback, concurrency and error-to-status mapping;
feed specific corrections into the next iteration. Tests are examples, not the whole spec.
