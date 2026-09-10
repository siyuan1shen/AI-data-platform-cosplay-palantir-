"""Reference PostgreSQL acceptance; CI supplies EI_TEST_POSTGRES_DSN.

Only point this at a disposable test database. The fixture owns a randomly named
schema and removes only that schema after the test.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enterprise_insight_backend.connectors import PostgreSQLConnector
from enterprise_insight_backend.errors import DomainError


@pytest.fixture
def postgres_source():
    dsn = os.environ.get("EI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set EI_TEST_POSTGRES_DSN to run against a disposable PostgreSQL database")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    config = conninfo_to_dict(dsn)
    profile = {
        "host": config.get("host", "127.0.0.1"),
        "port": int(config.get("port", 5432)),
        "database": config["dbname"],
        "user": config["user"],
        "password": config.get("password"),
        "sslmode": config.get("sslmode", "prefer"),
        "max_retries": 0,
    }
    schema = f"ei_acceptance_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL(
                "CREATE TABLE {}.orders (id integer PRIMARY KEY, updated_at timestamptz "
                "NOT NULL, amount numeric(12,2) NOT NULL)"
            ).format(sql.Identifier(schema)))
            timestamp = datetime(2026, 9, 8, tzinfo=UTC)
            for index in range(1, 6):
                connection.execute(sql.SQL(
                    "INSERT INTO {}.orders VALUES (%s, %s, %s)"
                ).format(sql.Identifier(schema)), (index, timestamp, index * 10))
            yield PostgreSQLConnector(profile), connection, schema, sql
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_postgresql_composite_cursor_has_no_gaps_or_duplicates(postgres_source) -> None:
    connector, connection, schema, sql = postgres_source
    assert connector.test()[0]
    watermark = tie_breaker = None
    seen = []
    for _ in range(4):
        page = connector.extract(
            f"{schema}.orders", limit=2, watermark_column="updated_at",
            tie_breaker_column="id", after_watermark=watermark, after_tie_breaker=tie_breaker,
        )
        seen.extend(row["id"] for row in page.rows)
        if not page.rows:
            break
        watermark, tie_breaker = page.next_watermark, page.next_tie_breaker
    assert seen == [1, 2, 3, 4, 5]
    assert connection.execute(sql.SQL("SELECT count(*) FROM {}.orders").format(
        sql.Identifier(schema)
    )).fetchone()[0] == 5


def test_real_postgresql_source_side_effect_is_blocked_by_read_only_transaction(
    postgres_source,
) -> None:
    connector, connection, schema, sql = postgres_source
    connection.execute(sql.SQL(
        "CREATE FUNCTION {}.attempt_write() RETURNS integer LANGUAGE plpgsql AS $$ "
        "BEGIN INSERT INTO {}.orders VALUES (99, now(), 99); RETURN 99; END $$"
    ).format(sql.Identifier(schema), sql.Identifier(schema)))
    connection.execute(sql.SQL(
        "CREATE VIEW {}.write_view AS SELECT {}.attempt_write() AS id"
    ).format(sql.Identifier(schema), sql.Identifier(schema)))
    with pytest.raises(DomainError) as error:
        connector.extract(
            f"{schema}.write_view", limit=1, watermark_column=None, tie_breaker_column=None,
            after_watermark=None, after_tie_breaker=None,
        )
    assert error.value.code == "CONNECTOR_EXTRACT_FAILED"
    assert connection.execute(sql.SQL("SELECT count(*) FROM {}.orders WHERE id = 99").format(
        sql.Identifier(schema)
    )).fetchone()[0] == 0


def test_real_postgresql_api_confirmation_uses_the_previewed_rows(postgres_source, client) -> None:
    connector, connection, schema, sql = postgres_source
    company = client.post("/api/v3/companies", json={"name": "真实驱动合成验收"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "数据库快照确认"}
    ).json()
    base = f"/api/v3/projects/{project['id']}"
    source_response = client.post(f"{base}/source-systems", json={
        "name": "临时 PostgreSQL", "kind": "POSTGRESQL",
        "connection_profile": connector.profile,
    })
    assert source_response.status_code == 201, source_response.text
    source_id = source_response.json()["id"]
    preview = client.post(f"{base}/source-systems/{source_id}/extract/preview", json={
        "asset_key": f"{schema}.orders", "limit": 2,
        "watermark_column": "updated_at", "tie_breaker_column": "id",
    })
    assert preview.status_code == 200, preview.text
    snapshot = preview.json()
    connection.execute(sql.SQL("UPDATE {}.orders SET amount = 999 WHERE id = 1").format(
        sql.Identifier(schema)
    ))
    confirmation = client.post(f"{base}/source-systems/{source_id}/sync", json={
        "preview_id": snapshot["preview_id"],
    })
    assert confirmation.status_code == 200, confirmation.text
    batches = client.get(f"{base}/raw-batches").json()["items"]
    assert len(batches) == 1
    records = client.get(f"{base}/raw-batches/{batches[0]['id']}/records").json()["items"]
    assert len(records) == 2
    amounts = {str(item["payload"]["id"]): str(item["payload"]["amount"]) for item in records}
    assert float(amounts["1"]) == 10
    repeated = client.post(f"{base}/source-systems/{source_id}/sync", json={
        "preview_id": snapshot["preview_id"],
    })
    assert repeated.status_code == 409
