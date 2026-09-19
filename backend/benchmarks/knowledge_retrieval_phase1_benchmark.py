# -*- coding: utf-8 -*-
"""Phase 1 Knowledge Retrieval Benchmark (T8).

Validates Section 10.2 performance targets and budget limits across:
- Regular dataset: 710 real world entries (598 enabled) + 290 characters = 1,000 entities
- Stress dataset: 710 real world entries (598 enabled) + 4,290 characters = 5,000 entities

Hard thresholds:
- Regular search P95 <= 300 ms
- Regular 10-item bounded batch read P95 <= 200 ms
- Stress character search P95 <= 1,000 ms
- Stress world entries search P95 <= 300 ms
- Business SELECT counts: search <= 6, batch read <= 3
- All serialized responses <= 32,768 characters
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import gc
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
from time import perf_counter_ns
import tracemalloc
from typing import Any, Callable, Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.settings
from app.core.text_normalization import normalize_literal
from app.storage.database import ALEMBIC_INI_PATH
from app.storage.services import knowledge_read_service, knowledge_search_service
from app.storage.services.knowledge_contracts import (
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    KnowledgeSearchRequest,
    SearchMatchMode,
)
from benchmarks.knowledge_retrieval_baseline import (
    DEFAULT_SEED,
    REGULAR_CHARACTER_COUNT,
    STRESS_CHARACTER_COUNT,
    GeneratedCharacter,
    backup_database,
    generate_characters,
)

EXPECTED_WORLD_FINGERPRINT = (
    "sha256:2986a0b06d96b50e2702bd3defbd763fd63e5ddc6e542d83081cee063054956e"
)


def compute_world_book_fingerprint(conn: sqlite3.Connection, world_info_id: str) -> str:
    cursor = conn.execute(
        """
        SELECT id, name, content, is_enabled
        FROM world_info_entries
        WHERE world_info_id = ?
        ORDER BY id ASC
        """,
        (world_info_id,),
    )
    rows = [
        {
            "id": row[0],
            "name": row[1],
            "content": row[2],
            "is_enabled": row[3],
        }
        for row in cursor.fetchall()
    ]
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def populate_characters_and_aliases(
    conn: sqlite3.Connection,
    project_id: str,
    characters: Sequence[GeneratedCharacter],
) -> None:
    conn.executemany(
        """
        INSERT INTO characters (
            id, project_id, name, description, image_path, is_favorited,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
        """,
        [
            (
                c.id,
                project_id,
                c.name,
                c.description,
                c.created_at,
                c.updated_at,
            )
            for c in characters
        ],
    )
    alias_rows = []
    for c in characters:
        for pos, alias in enumerate(c.aliases):
            alias_id = f"c_alias_{c.id}_{pos}"
            normalized = normalize_literal(alias)
            alias_rows.append((alias_id, c.id, alias, normalized, pos))
    if alias_rows:
        conn.executemany(
            """
            INSERT INTO character_aliases (
                id, character_id, alias, normalized_alias, position
            ) VALUES (?, ?, ?, ?, ?)
            """,
            alias_rows,
        )


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


class QueryCounter:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.count = 0
        self._listener = self._on_execute

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        clean = statement.strip().upper()
        if clean.startswith("SELECT") and not clean.startswith("SELECT 1 FROM AL_"):
            self.count += 1

    def start(self):
        self.count = 0
        event.listen(self.engine, "before_cursor_execute", self._listener)

    def stop(self) -> int:
        event.remove(self.engine, "before_cursor_execute", self._listener)
        return self.count


async def measure_query(
    action_fn: Callable[[AsyncSession], Any],
    session_factory: Any,
    raw_engine: Engine,
    *,
    warmups: int = 10,
    iterations: int = 100,
) -> dict[str, Any]:
    # 1. Cold start measurement
    gc.collect()
    t0 = perf_counter_ns()
    async with session_factory() as session:
        await action_fn(session)
    cold_ms = (perf_counter_ns() - t0) / 1_000_000

    # Measure business query count on one invocation
    counter = QueryCounter(raw_engine)
    counter.start()
    async with session_factory() as session:
        measured_result = await action_fn(session)
    business_selects = counter.stop()

    # 2. Warmups
    for _ in range(warmups):
        async with session_factory() as session:
            await action_fn(session)

    # 3. Iterations with memory tracking
    tracemalloc.start()
    latencies: list[float] = []
    for _ in range(iterations):
        t_start = perf_counter_ns()
        async with session_factory() as session:
            await action_fn(session)
        latencies.append((perf_counter_ns() - t_start) / 1_000_000)
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 4. Output serialization sizes
    if hasattr(measured_result, "model_dump_json"):
        serialized_json = measured_result.model_dump_json(exclude_none=True)
    elif hasattr(measured_result, "model_dump"):
        serialized_json = json.dumps(measured_result.model_dump(exclude_none=True), ensure_ascii=False)
    else:
        serialized_json = json.dumps(measured_result, ensure_ascii=False)

    output_chars = len(serialized_json)
    output_bytes = len(serialized_json.encode("utf-8"))
    estimated_tokens = math.ceil(output_chars / 2.5)

    return {
        "timing_ms": {
            "cold": round(cold_ms, 3),
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "mean": round(statistics.mean(latencies), 3),
            "p95": round(_nearest_rank(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "business_selects": business_selects,
        "output_chars": output_chars,
        "output_bytes": output_bytes,
        "estimated_tokens": estimated_tokens,
        "peak_memory_kb": round(peak_mem / 1024, 2),
        "iterations": iterations,
    }


async def run_benchmark(
    db_source: Path,
    output_path: Path | None = None,
    warmups: int = 10,
    iterations: int = 100,
) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="openfic_bench_"))
    try:
        reg_db_path = temp_dir / "regular.db"
        stress_db_path = temp_dir / "stress.db"

        # Backup database
        backup_database(db_source, reg_db_path)

        # Connect to verify world book and get IDs
        conn = sqlite3.connect(reg_db_path)
        world_row = conn.execute("SELECT id, project_id FROM world_info LIMIT 1").fetchone()
        if not world_row:
            raise RuntimeError("世界书不存在")
        world_info_id, project_id = world_row[0], world_row[1]

        # Fingerprint verification
        fingerprint = compute_world_book_fingerprint(conn, world_info_id)
        if fingerprint != EXPECTED_WORLD_FINGERPRINT:
            raise RuntimeError(
                f"世界书逻辑指纹不匹配: 期望 {EXPECTED_WORLD_FINGERPRINT}，实际 {fingerprint}"
            )

        # Upgrade database schema via alembic
        app.settings.BACKEND_DATA_DIR = temp_dir
        # Rename temporarily to openfic.db for alembic env.py
        openfic_db = temp_dir / "openfic.db"
        shutil.copy2(reg_db_path, openfic_db)
        alembic_cfg = Config(str(ALEMBIC_INI_PATH))
        command.upgrade(alembic_cfg, "head")
        shutil.copy2(openfic_db, reg_db_path)
        openfic_db.unlink()

        # Reopen and check alembic version
        conn.close()
        conn = sqlite3.connect(reg_db_path)
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if ver != "1024":
            raise RuntimeError(f"Alembic 迁移版本异常: {ver}")

        # Materialize regular characters (290)
        reg_characters = list(generate_characters(project_id, count=REGULAR_CHARACTER_COUNT, seed=DEFAULT_SEED))
        populate_characters_and_aliases(conn, project_id, reg_characters)
        conn.commit()

        # Copy to stress db and append to 4,290 characters
        backup_database(reg_db_path, stress_db_path)
        stress_conn = sqlite3.connect(stress_db_path)
        stress_characters = list(generate_characters(project_id, count=STRESS_CHARACTER_COUNT, seed=DEFAULT_SEED))[REGULAR_CHARACTER_COUNT:]
        populate_characters_and_aliases(stress_conn, project_id, stress_characters)
        stress_conn.commit()
        stress_conn.close()

        # Fetch sample target IDs
        world_entry_rows = conn.execute(
            "SELECT id, name FROM world_info_entries WHERE world_info_id = ? AND is_enabled = 1 ORDER BY id ASC LIMIT 10",
            (world_info_id,),
        ).fetchall()
        sample_world_entry_ids = [r[0] for r in world_entry_rows]

        longest_world_entry = conn.execute(
            "SELECT id, name, length(content) FROM world_info_entries WHERE world_info_id = ? AND is_enabled = 1 ORDER BY length(content) DESC LIMIT 1",
            (world_info_id,),
        ).fetchone()
        longest_world_entry_id = longest_world_entry[0]
        longest_world_entry_chars = longest_world_entry[2]

        sample_char_rows = conn.execute(
            "SELECT id, name FROM characters WHERE project_id = ? ORDER BY id ASC LIMIT 10",
            (project_id,),
        ).fetchall()
        sample_character_ids = [r[0] for r in sample_char_rows]
        conn.close()

        # Setup SQLAlchemy async engines
        reg_engine = create_async_engine(f"sqlite+aiosqlite:///{reg_db_path.as_posix()}")
        reg_session_factory = async_sessionmaker(reg_engine, expire_on_commit=False)

        stress_engine = create_async_engine(f"sqlite+aiosqlite:///{stress_db_path.as_posix()}")
        stress_session_factory = async_sessionmaker(stress_engine, expire_on_commit=False)

        # -------------------------------------------------------------
        # Regular Benchmarks
        # -------------------------------------------------------------
        benchmarks: dict[str, Any] = {"regular": {}, "stress": {}}

        # 1. World Search Exact Title
        benchmarks["regular"]["world_search_exact_name"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="燃魂术")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 2. World Search Content Only ("寿命" not in title)
        benchmarks["regular"]["world_search_content_only"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="寿命")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 3. World Search Multi Term All ("燃魂 寿命")
        benchmarks["regular"]["world_search_multi_term"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s,
                project_id,
                KnowledgeSearchRequest(query="燃魂 寿命", match=SearchMatchMode.ALL),
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 4. World Search Common Term Paged ("修", limit=10)
        benchmarks["regular"]["world_search_common_paged"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="修", limit=10)
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 5. World Search No Match
        benchmarks["regular"]["world_search_no_match"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="绝对不存在的功法名称xyz")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 6. Character Search Exact Name ("基准角色0001")
        benchmarks["regular"]["character_search_name"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="基准角色0001")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 7. Character Search Alias ("别称0001")
        benchmarks["regular"]["character_search_alias"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="别称0001")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 8. Character Search Content ("专属线索稀有词01")
        benchmarks["regular"]["character_search_content"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="专属线索稀有词01")
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 9. Bounded Batch Read (10 World Entries)
        benchmarks["regular"]["world_read_bounded_10"] = await measure_query(
            lambda s: knowledge_read_service.read_world_entries(
                s,
                project_id,
                KnowledgeReadRequest(
                    items=[KnowledgeReadItemRequest(id=eid) for eid in sample_world_entry_ids],
                    max_chars_per_item=1000,
                ),
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 10. Bounded Batch Read (10 Characters)
        benchmarks["regular"]["character_read_bounded_10"] = await measure_query(
            lambda s: knowledge_read_service.read_characters(
                s,
                project_id,
                KnowledgeReadRequest(
                    items=[KnowledgeReadItemRequest(id=cid) for cid in sample_character_ids],
                    max_chars_per_item=1000,
                ),
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 11. Long Content Read & Truncation (15,534 chars entry with max_chars_per_item=8000)
        benchmarks["regular"]["world_read_longest_truncated"] = await measure_query(
            lambda s: knowledge_read_service.read_world_entries(
                s,
                project_id,
                KnowledgeReadRequest(
                    items=[KnowledgeReadItemRequest(id=longest_world_entry_id, start_offset=0)],
                    max_chars_per_item=8000,
                ),
            ),
            reg_session_factory,
            reg_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # -------------------------------------------------------------
        # Stress Benchmarks (4,290 Characters + 710 World Entries)
        # -------------------------------------------------------------
        # 1. Stress Character Search Name ("基准角色1234")
        benchmarks["stress"]["character_search_name"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="基准角色1234")
            ),
            stress_session_factory,
            stress_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 2. Stress Character Search Alias ("别称1234")
        benchmarks["stress"]["character_search_alias"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="别称1234")
            ),
            stress_session_factory,
            stress_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 3. Stress Character Search Content ("专属线索稀有词42")
        benchmarks["stress"]["character_search_content"] = await measure_query(
            lambda s: knowledge_search_service.search_characters(
                s, project_id, KnowledgeSearchRequest(query="专属线索稀有词42")
            ),
            stress_session_factory,
            stress_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 4. Stress World Search (Verify scale of characters does NOT degrade world search)
        benchmarks["stress"]["world_search_exact_name"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="燃魂术")
            ),
            stress_session_factory,
            stress_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        # 5. Stress World Search Content ("寿命")
        benchmarks["stress"]["world_search_content_only"] = await measure_query(
            lambda s: knowledge_search_service.search_world_entries(
                s, project_id, KnowledgeSearchRequest(query="寿命")
            ),
            stress_session_factory,
            stress_engine.sync_engine,
            warmups=warmups,
            iterations=iterations,
        )

        await reg_engine.dispose()
        await stress_engine.dispose()

        report = {
            "meta": {
                "generated_at": datetime.now(UTC).isoformat(),
                "world_book_fingerprint": fingerprint,
                "project_id": project_id,
                "world_info_id": world_info_id,
                "dataset_counts": {
                    "regular": {"world_entries": 710, "characters": 290, "total": 1000, "searchable": 888},
                    "stress": {"world_entries": 710, "characters": 4290, "total": 5000, "searchable": 4888},
                },
                "longest_world_entry": {
                    "id": longest_world_entry_id,
                    "name": longest_world_entry[1],
                    "chars": longest_world_entry_chars,
                },
                "environment": {
                    "os": platform.platform(),
                    "machine": platform.machine(),
                    "processor": platform.processor(),
                    "python": platform.python_version(),
                    "sqlite": sqlite3.sqlite_version,
                    "source_db_bytes": db_source.stat().st_size,
                    "regular_db_bytes": reg_db_path.stat().st_size,
                    "stress_db_bytes": stress_db_path.stat().st_size,
                },
            },
            "benchmarks": benchmarks,
        }

        # Validate hard thresholds
        threshold_violations: list[str] = []

        for category, runs in benchmarks.items():
            for name, data in runs.items():
                p95 = data["timing_ms"]["p95"]
                selects = data["business_selects"]
                chars = data["output_chars"]

                # Check output budget <= 32,768
                if chars > 32768:
                    threshold_violations.append(f"[{category}.{name}] 输出字符数 {chars} 超过 32,768 上限")

                # Check SELECT count
                if "search" in name and selects > 6:
                    threshold_violations.append(f"[{category}.{name}] 搜索 SELECT 次数 {selects} 超过 6 次上限")
                elif "read" in name and selects > 3:
                    threshold_violations.append(f"[{category}.{name}] 读取 SELECT 次数 {selects} 超过 3 次上限")

                # Check Latency P95
                if category == "regular":
                    if "search" in name and p95 > 300.0:
                        threshold_violations.append(f"[{category}.{name}] 常规搜索 P95 {p95}ms 超过 300ms 上限")
                    elif "read" in name and p95 > 200.0:
                        threshold_violations.append(f"[{category}.{name}] 常规读取 P95 {p95}ms 超过 200ms 上限")
                elif category == "stress":
                    if "character_search" in name and p95 > 1000.0:
                        threshold_violations.append(f"[{category}.{name}] 压力角色搜索 P95 {p95}ms 超过 1,000ms 上限")
                    elif "world_search" in name and p95 > 300.0:
                        threshold_violations.append(f"[{category}.{name}] 压力世界书搜索 P95 {p95}ms 超过 300ms 上限")

        report["verification"] = {
            "passed": len(threshold_violations) == 0,
            "violations": threshold_violations,
        }

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return report

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/openfic.db"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/reports/phase1_benchmark_report.json"))
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    report = asyncio.run(run_benchmark(args.database, output_path=args.output, warmups=args.warmups, iterations=args.iterations))
    print(json.dumps(report["verification"], ensure_ascii=False, indent=2))
    if not report["verification"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
