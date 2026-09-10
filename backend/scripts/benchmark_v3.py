from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.models import (
    CompanyRow,
    EntityRow,
    ProjectRow,
    RelationParticipantRow,
    RelationRow,
)
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import GraphQuery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the V3 local graph read path.")
    parser.add_argument("--entities", type=int, default=10_000)
    parser.add_argument("--relations", type=int, default=30_000)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep-database", type=Path)
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def chunks[T](values: list[T], size: int = 2_000):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def seed(database: Database, entity_count: int, relation_count: int) -> tuple[UUID, UUID]:
    timestamp = datetime.now(UTC)
    with database.session_factory() as session:
        company = CompanyRow(name="V3 性能基准企业", industry="合成数据")
        session.add(company)
        session.flush()
        project = ProjectRow(company_id=company.id, name="标准规模图查询")
        session.add(project)
        session.flush()
        project_id = project.id
        entity_ids = [str(uuid4()) for _ in range(entity_count)]
        entity_rows = [
            {
                "id": entity_id,
                "project_id": project_id,
                "type_key": "organization_unit",
                "stable_key": f"unit.{index:05d}",
                "name": f"部门{index:05d}",
                "properties": {"mandate": "合成基准职责"},
                "design_membership": "MODELED",
                "viewpoint": "DESIGNED",
                "evidence": [],
                "status": "DRAFT",
                "revision": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            for index, entity_id in enumerate(entity_ids)
        ]
        for batch in chunks(entity_rows):
            session.execute(insert(EntityRow), batch)

        relation_rows = []
        participant_rows = []
        for index in range(relation_count):
            relation_id = str(uuid4())
            source_index = index % entity_count
            target_index = (source_index + 1 + index // entity_count) % entity_count
            relation_rows.append(
                {
                    "id": relation_id,
                    "project_id": project_id,
                    "type_key": "contains",
                    "name": None,
                    "properties": {},
                    "viewpoint": "DESIGNED",
                    "evidence": [],
                    "status": "DRAFT",
                    "revision": 1,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            participant_rows.extend(
                [
                    {
                        "id": str(uuid4()),
                        "relation_id": relation_id,
                        "role_key": "container",
                        "entity_id": entity_ids[source_index],
                        "ordinal": 0,
                    },
                    {
                        "id": str(uuid4()),
                        "relation_id": relation_id,
                        "role_key": "member",
                        "entity_id": entity_ids[target_index],
                        "ordinal": 0,
                    },
                ]
            )
        for batch in chunks(relation_rows):
            session.execute(insert(RelationRow), batch)
        for batch in chunks(participant_rows):
            session.execute(insert(RelationParticipantRow), batch)
        session.commit()
        return UUID(project_id), UUID(entity_ids[0])


def benchmark(args: argparse.Namespace) -> dict[str, object]:
    if args.keep_database:
        database_path = args.keep_database.resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if database_path.exists():
            raise RuntimeError(
                f"Refusing to overwrite existing benchmark database: {database_path}"
            )
        database_url = f"sqlite:///{database_path.as_posix()}"
        data_dir = database_path.parent
    else:
        database_path = None
        database_url = "sqlite:///:memory:"
        data_dir = Path.cwd() / "artifacts"
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        frontend_dist_dir=None,
        agent_worker_enabled=False,
    )
    database = Database(settings)
    print(
        f"Preparing benchmark database: {database_path or 'memory'}",
        file=sys.stderr,
        flush=True,
    )
    database.create_schema()
    started = time.perf_counter()
    print(
        f"Seeding {args.entities} entities and {args.relations} relations...",
        file=sys.stderr,
        flush=True,
    )
    project_id, root_id = seed(database, args.entities, args.relations)
    seed_seconds = time.perf_counter() - started

    graph_times: list[float] = []
    list_times: list[float] = []
    tracemalloc.start()
    print("Running local graph and filtered-list samples...", file=sys.stderr, flush=True)
    with database.session_factory() as session:
        service = ProjectionService(session)
        service.graph(project_id, GraphQuery(root_entity_id=root_id, depth=2))
        for _ in range(args.iterations):
            started = time.perf_counter()
            graph = service.graph(project_id, GraphQuery(root_entity_id=root_id, depth=2))
            graph_times.append((time.perf_counter() - started) * 1_000)
            started = time.perf_counter()
            page = service.list_entities(
                project_id,
                search="部门00042",
                include_observations=False,
                limit=200,
            )
            list_times.append((time.perf_counter() - started) * 1_000)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    database.engine.dispose()

    graph_p95 = percentile(graph_times, 0.95)
    list_p95 = percentile(list_times, 0.95)
    result: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
        },
        "scale": {
            "entities": args.entities,
            "relations": args.relations,
            "iterations": args.iterations,
        },
        "seed_seconds": round(seed_seconds, 3),
        "local_graph": {
            "p50_ms": round(statistics.median(graph_times), 3),
            "p95_ms": round(graph_p95, 3),
            "returned_entities": len(graph.entities),
            "returned_relations": len(graph.relations),
        },
        "filtered_entity_page": {
            "p50_ms": round(statistics.median(list_times), 3),
            "p95_ms": round(list_p95, 3),
            "returned_entities": len(page),
        },
        "python_peak_mib_during_queries": round(peak_bytes / 1024 / 1024, 3),
        "target": {"local_graph_p95_ms": 500, "filtered_page_p95_ms": 500},
        "passed": graph_p95 <= 500 and list_p95 <= 500,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return result


def main() -> None:
    args = parse_args()
    if args.entities < 2 or args.relations < 1 or args.iterations < 1:
        raise SystemExit("entities >= 2, relations >= 1 and iterations >= 1 are required")
    print(json.dumps(benchmark(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
