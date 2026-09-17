# Enterprise Insight Task Bridge (Frappe v16)

An independently installable Frappe app that exposes a deliberately narrow API for creating, updating, reading, and cancelling ERPNext `Task` documents. It is a bridge contract, not a general-purpose remote execution facility.

## Fixed source contract reviewed

Implementation was checked against the local, read-only source snapshots:

- ERPNext `v16.34.2`, commit `4048fb70e14d1843956fcdabb7c3cca75a1cbcdd`
- Frappe `v16.33.1`, commit `988e54f3c4c291e2077a83809663f123731abe76`

The ERPNext `Task` DocType is not a submit/cancel DocType. Cancellation here means an ordinary, permission-checked Task save with `status="Cancelled"`, not `doc.cancel()` or a DocStatus transition. The bridge uses Frappe's request transaction and database row locking; it does not issue custom SQL or commit mid-request.

## API

Both methods use Frappe's standard `{ "message": ... }` response envelope. Frappe adds that envelope around the method's returned object.

### Execute

`POST /api/method/enterprise_insight_task_bridge.api.execute_task_operation`

Form or JSON fields:

```json
{
  "operation_id": "platform-op-000042",
  "operation": "UPDATE",
  "payload": {"name": "TASK-00042", "priority": "High"},
  "payload_sha256": "<lowercase sha256 of canonical payload JSON>",
  "expected_modified": "2026-09-13 10:30:00.000000"
}
```

`action` is accepted as an alias for `operation`. If both are supplied, they must identify the same operation. Allowed operations are `CREATE`, `UPDATE`, `READ`, and `CANCEL`.

`payload_sha256` is SHA-256 of the payload object serialized as UTF-8 JSON with sorted keys, no insignificant whitespace, Unicode preserved, and non-finite numbers rejected. The expected modification timestamp is stored and compared independently as part of the operation identity. Reusing an `operation_id` with a different action, digest, or expected timestamp returns `CONFLICT`; the original receipt is never overwritten.

Payload shapes:

- `CREATE`: Task fields from the allowlist below; `subject` is required. The bridge leaves Task status at ERPNext's default (`Open`).
- `UPDATE`: `{"name": "TASK-...", ...allowed mutable fields...}` and a non-empty `expected_modified`.
- `READ`: exactly `{"name": "TASK-..."}`.
- `CANCEL`: exactly `{"name": "TASK-..."}` and a non-empty `expected_modified`.

The Task field allowlist is `subject`, `project`, `description`, `priority`, `exp_start_date`, `exp_end_date`, `expected_time`, `progress`, `is_milestone`, `department`, `type`, `issue`, and `parent_task`. `name` is only a target identifier for `UPDATE`, `READ`, and `CANCEL`; callers cannot choose a Task name during creation. `company`, `is_group`, dependencies, assignments, docstatus, and arbitrary fields are not exposed.

Status changes through `UPDATE` follow this explicit policy (ERPNext's own Task validation still applies):

| Current status | Allowed next status |
|---|---|
| Open | Working, Pending Review, Cancelled |
| Working | Open, Pending Review, Completed, Cancelled |
| Pending Review | Working, Completed, Cancelled |
| Overdue | Open, Working, Pending Review, Completed, Cancelled |
| Template, Completed, Cancelled | no transition |

Setting status to its current value is a no-op. `CANCEL` allows any non-terminal status to become `Cancelled`; an already-cancelled task is a no-op. A completed task cannot be cancelled by this bridge.

Successful executions return a receipt with `status="COMMITTED"`; business outcomes may return `NOT_FOUND`, `CONFLICT`, or `REJECTED`. An unexpected error that can be rolled back to the operation savepoint returns `FAILED`. An operation inserted but not yet finalized may be reported as `IN_PROGRESS`, though it is not normally visible to another transaction because the operation row and Task mutation commit together.

### Look up a receipt

`GET /api/method/enterprise_insight_task_bridge.api.get_task_operation?operation_id=platform-op-000042`

Returns the original stored receipt for a completed operation, `IN_PROGRESS` if a committed in-progress row is encountered, or `NOT_FOUND`. A caller other than the user that created the operation sees `NOT_FOUND` to avoid exposing another caller's Task result. The `System Manager` role may inspect any operation.

## Idempotency, concurrency, and errors

- `Task Operation` uses `operation_id` as its Frappe document name, so the database primary key enforces uniqueness in the remote transaction. The initial operation row, Task action, and final receipt participate in the same request transaction.
- Repeating the same operation ID and identical descriptor returns the exact original stored receipt; reusing it with a different descriptor returns `CONFLICT`.
- UPDATE and CANCEL lock the target Task row before comparing `expected_modified`, then use the ordinary Frappe document save path. A stale timestamp returns and stores `CONFLICT` without changing the Task.
- Invalid field shapes, invalid transitions, permission denial, missing Task, and ERPNext validation failures are recorded as terminal receipts when the operation row has been validly registered.
- Malformed JSON, an invalid operation ID, or a payload digest mismatch is rejected before registration. Such input cannot reserve an operation ID.
- If the client times out, the outcome is unknown until checked. First call the GET lookup. If it returns `NOT_FOUND`, retry the exact same POST with the same operation ID and request; never create a fresh ID just because the response timed out. If it returns a terminal status, use that receipt as the result.
- A process/server crash before Frappe commits can leave no operation row and no Task mutation. A crash after commit but before the client receives the response is resolved through the GET receipt.
- Frappe authenticates the request; Guest access is not enabled. Native Task create/read/write permissions and document validation remain in force. Receipts are scoped to their initiating user (except System Manager).

## Install on a Frappe bench

Copy this directory into the bench's `apps/enterprise_insight_task_bridge` directory (or install this directory from its own Git remote), then run:

```text
bench setup requirements
bench --site <site-name> install-app enterprise_insight_task_bridge
bench --site <site-name> migrate
```

Use an authenticated Frappe integration user with the required native Task permissions. Do not grant guest access or add broad Task write permissions merely to make the bridge work.

## Tests and verification boundary

Run the self-contained tests from this directory with:

```text
python -m unittest discover -s tests -v
```

The tests cover protocol normalization, digest verification, payload and field allowlists, status transitions, API method annotations, and DocType metadata. They do not start a Frappe site or verify a live ERPNext installation. No Docker/WSL-backed site test is claimed.
