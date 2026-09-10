# Enterprise Insight Backend

This directory owns the `/api/v3` backend, database schema, migrations, domain services,
workers, and backend tests. The frontend is intentionally outside this directory.

Run locally:

```powershell
python -m enterprise_insight_backend
```

The current backend includes:

- a versioned ontology and typed enterprise graph;
- evidence, import preview/confirm, lineage, publication, and offline exports;
- persistent Agent queues and approval-gated Actions;
- system-field-to-ontology mappings;
- management metrics, observations, meeting records, and explicit design trade-offs;
- deterministic design/outcome signal generation and cross-side reconciliation;
- typed, falsifiable causal hypotheses and management feedback;
- persistent Agent golden sets, deterministic scoring, and regression history.

Management analysis never writes directly into the trusted enterprise graph. It produces
reviewable signals, insights, hypotheses, scenarios, and information requests. Only the
projection and ontology Actions can mutate trusted structures, and those Actions require
preflight validation and approval.

Run checks from this directory:

```powershell
ruff check src tests
pytest
```

The OpenAPI contract is generated into `../contracts/openapi.json` and is the only shared
frontend/backend API contract.

Application startup is the supported migration entry point. Existing SQLite databases
receive a consistent pre-upgrade copy in a sibling `schema-backups/` directory. Batch
table rebuilds run in an explicit transaction with foreign-key integrity checked before
commit; normal runtime connections continue enforcing foreign keys. Failed upgrades
stop startup and identify the retained backup. Unversioned databases are adopted only
after checking columns, types, nullability, primary/unique/foreign keys and indexes.
Already-current databases do not create another backup on each startup.

Real PostgreSQL connector acceptance lives in `tests/test_postgresql_live.py`.
Set `EI_TEST_POSTGRES_DSN` to a **disposable test database** before running pytest.
The fixture creates a random `ei_acceptance_*` schema and removes only that schema.
It checks composite cursor pagination, read-only enforcement against a writing view,
and API preview/confirmation while the source changes. Without this variable these
tests are explicitly skipped, not treated as proof of connector compatibility.
CI configures PostgreSQL 16 and runs these tests, then builds and smoke-tests the V3
container's API and frontend. The workflow must actually run before claiming those
environment-dependent checks passed.
