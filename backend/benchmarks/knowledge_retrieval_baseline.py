"""T0 fixtures and read-only baselines for phase-one knowledge retrieval.

The source database is always opened through SQLite's ``mode=ro`` URI and
``query_only`` pragma.  Use :func:`backup_database` before a migration or load
test; it relies on SQLite's backup API so WAL-backed state is not missed.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import sys
from time import perf_counter_ns
from typing import Any

DEFAULT_SEED = 20260918
REGULAR_CHARACTER_COUNT = 290
STRESS_CHARACTER_COUNT = 4290
FIXED_TIMESTAMP = datetime(2026, 9, 18, tzinfo=UTC)


@dataclass(frozen=True)
class GeneratedCharacter:
    id: str
    project_id: str
    name: str
    description: str
    aliases: tuple[str, ...]
    created_at: str
    updated_at: str


def _stable_id(seed: int, index: int) -> str:
    digest = hashlib.sha256(f"knowledge-character:{seed}:{index}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:21]


def _description_of_length(index: int, target: int) -> str:
    marker = (
        f"角色编号{index:04d}，代号CODE-{index:04d}。"
        f"专属线索稀有词{index % 97:02d}。"
    )
    paragraph = (
        "人物记录包含来历、动机、能力、关系与当前目标；"
        "这些文字用于稳定的中文连续子串和英文 ASCII 匹配测试。\n"
    )
    text = marker + paragraph
    if len(text) < target:
        repeats = math.ceil((target - len(text)) / len(paragraph))
        text += paragraph * repeats
    return text[:target]


def generate_characters(
    project_id: str,
    *,
    count: int = STRESS_CHARACTER_COUNT,
    seed: int = DEFAULT_SEED,
) -> Iterator[GeneratedCharacter]:
    """Yield deterministic character fixtures without touching a database.

    A local RNG is used so callers' global random state is unaffected.  The
    log-normal length distribution has a target median near 2,000 characters
    and nearest-rank P95 near 10,000 characters at the stress fixture size.
    """

    if not project_id:
        raise ValueError("project_id 不能为空")
    if count < 0:
        raise ValueError("count 不能小于 0")

    rng = random.Random(seed)
    sigma = math.log(5) / 1.6448536269514722
    for index in range(1, count + 1):
        length = min(100_000, max(200, round(rng.lognormvariate(math.log(2000), sigma))))
        alias_count = rng.randrange(4)
        alias_candidates = (
            f"别称{index:04d}",
            f"Alias-{index:04d}",
            f"共享称谓{index % 31:02d}",
        )
        timestamp = (FIXED_TIMESTAMP + timedelta(seconds=index)).isoformat()
        yield GeneratedCharacter(
            id=_stable_id(seed, index),
            project_id=project_id,
            name=f"基准角色{index:04d}",
            description=_description_of_length(index, length),
            aliases=alias_candidates[:alias_count],
            created_at=timestamp,
            updated_at=timestamp,
        )


def fixture_digest(characters: Sequence[GeneratedCharacter]) -> str:
    hasher = hashlib.sha256()
    for character in characters:
        encoded = json.dumps(
            asdict(character),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        hasher.update(encoded)
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def write_fixture_jsonl(path: Path, characters: Sequence[GeneratedCharacter]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for character in characters:
            output.write(
                json.dumps(
                    asdict(character),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            output.write("\n")


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def backup_database(source: Path, destination: Path) -> None:
    """Create a consistent writable copy with SQLite's online backup API."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    source_connection = connect_read_only(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def materialize_characters(
    connection: sqlite3.Connection,
    characters: Sequence[GeneratedCharacter],
) -> None:
    """Insert old-schema character fields into a writable benchmark copy.

    Alias rows intentionally remain deferred until T1/T2.  Requiring an empty
    target project makes the physical 1,000/5,000-row fixture totals explicit
    and prevents accidental append-to-user-data behavior.
    """

    if not characters:
        return
    project_ids = {character.project_id for character in characters}
    if len(project_ids) != 1:
        raise ValueError("所有生成角色必须属于同一项目")
    project_id = next(iter(project_ids))
    project_exists = connection.execute(
        "SELECT 1 FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if project_exists is None:
        raise ValueError(f"项目不存在: {project_id}")
    existing_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM characters WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
    )
    if existing_count:
        raise ValueError(f"目标项目已有 {existing_count} 个角色，拒绝混合基准样本")

    connection.executemany(
        """
        INSERT INTO characters (
            id, project_id, name, description, image_path, is_favorited,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
        """,
        [
            (
                character.id,
                character.project_id,
                character.name,
                character.description,
                character.created_at,
                character.updated_at,
            )
            for character in characters
        ],
    )


def prepare_fixture_database(
    source: Path,
    destination: Path,
    *,
    project_id: str,
    count: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Back up the source and add deterministic characters to the copy."""

    characters = list(generate_characters(project_id, count=count, seed=seed))
    backup_database(source, destination)
    connection = sqlite3.connect(destination)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        materialize_characters(connection, characters)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return fixture_summary(characters, seed=seed)


def _nearest_rank(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _select_world_book(connection: sqlite3.Connection, world_info_id: str | None) -> sqlite3.Row:
    if world_info_id is not None:
        row = connection.execute(
            "SELECT id, project_id FROM world_info WHERE id = ?",
            (world_info_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"世界书不存在: {world_info_id}")
        return row

    row = connection.execute(
        """
        SELECT wi.id, wi.project_id, COUNT(e.id) AS entry_count
        FROM world_info AS wi
        LEFT JOIN world_info_entries AS e ON e.world_info_id = wi.id
        GROUP BY wi.id, wi.project_id
        ORDER BY entry_count DESC, wi.id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise ValueError("数据库中没有世界书")
    return row


def capture_world_book_snapshot(
    connection: sqlite3.Connection,
    *,
    world_info_id: str | None = None,
) -> dict[str, Any]:
    world = _select_world_book(connection, world_info_id)
    rows = connection.execute(
        """
        SELECT id, name, content, is_enabled
        FROM world_info_entries
        WHERE world_info_id = ?
        ORDER BY id ASC
        """,
        (world["id"],),
    ).fetchall()
    canonical_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "content": row["content"],
            "is_enabled": row["is_enabled"],
        }
        for row in rows
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    lengths = [len(row["content"] or "") for row in rows]
    project_id = world["project_id"]
    character_count = 0
    if project_id is not None:
        character_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM characters WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
    return {
        "world_info_id": world["id"],
        "project_id": project_id,
        "entry_count": len(rows),
        "distinct_name_count": len({row["name"] for row in rows}),
        "enabled_count": sum(bool(row["is_enabled"]) for row in rows),
        "disabled_count": sum(not bool(row["is_enabled"]) for row in rows),
        "empty_content_count": sum(not (row["content"] or "") for row in rows),
        "content_total_chars": sum(lengths),
        "content_median_chars": int(statistics.median(lengths)) if lengths else 0,
        "content_p95_chars_nearest_rank": _nearest_rank(lengths, 0.95),
        "content_max_chars": max(lengths, default=0),
        "project_character_count": character_count,
        "logical_fingerprint": f"sha256:{fingerprint}",
    }


def _json_size(payload: object) -> dict[str, int]:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return {
        "characters": len(serialized),
        "utf8_bytes": len(serialized.encode("utf-8")),
    }


def _p95_ms(samples_ns: Sequence[int]) -> float:
    return round(_nearest_rank(samples_ns, 0.95) / 1_000_000, 3)


def _measure(operation, *, warmups: int, iterations: int) -> tuple[Any, dict[str, float | int]]:
    if warmups < 0 or iterations < 1:
        raise ValueError("warmups 必须 >= 0 且 iterations 必须 >= 1")
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    result: Any = None
    for _ in range(iterations):
        started = perf_counter_ns()
        result = operation()
        samples.append(perf_counter_ns() - started)
    return result, {
        "warmups": warmups,
        "iterations": iterations,
        "median_ms": round(statistics.median(samples) / 1_000_000, 3),
        "p95_ms": _p95_ms(samples),
    }


def capture_legacy_baseline(
    connection: sqlite3.Connection,
    *,
    world_info_id: str,
    project_id: str | None,
    warmups: int = 10,
    iterations: int = 100,
) -> dict[str, Any]:
    """Measure the pre-phase-one list and single-read query shapes.

    Payload contents and titles are intentionally omitted from the report.
    """

    def resolve_world_info_id() -> str:
        if project_id is None:
            raise RuntimeError("legacy Agent tools require a project-bound world book")
        row = connection.execute(
            "SELECT * FROM world_info WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("baseline project is no longer bound to a world book")
        return str(row["id"])

    def list_world_entries() -> dict[str, Any]:
        resolved_world_info_id = resolve_world_info_id()
        rows = connection.execute(
            """
            SELECT *
            FROM world_info_entries
            WHERE world_info_id = ? AND is_enabled = 1
            ORDER BY \"order\" ASC
            """,
            (resolved_world_info_id,),
        ).fetchall()
        return {
            "entries": [
                {"title": row["name"], "uid": row["uid"], "order": row["order"]}
                for row in rows
            ]
        }

    world_list, world_list_timing = _measure(
        list_world_entries, warmups=warmups, iterations=iterations
    )
    target = connection.execute(
        """
        SELECT name
        FROM world_info_entries
        WHERE world_info_id = ? AND is_enabled = 1
        ORDER BY length(content) DESC, id ASC
        LIMIT 1
        """,
        (world_info_id,),
    ).fetchone()

    world_read: dict[str, Any] | None = None
    if target is not None:
        target_name = target["name"]

        def read_world_entry() -> dict[str, Any]:
            resolved_world_info_id = resolve_world_info_id()
            rows = connection.execute(
                """
                SELECT *
                FROM world_info_entries
                WHERE world_info_id = ?
                ORDER BY \"order\" ASC
                """,
                (resolved_world_info_id,),
            ).fetchall()
            matches = [row for row in rows if row["name"] == target_name]
            if len(matches) != 1:
                raise RuntimeError("baseline target is no longer unique")
            row = matches[0]
            numbered = "\n".join(
                f"{number}|{line}"
                for number, line in enumerate((row["content"] or "").splitlines(), start=1)
            )
            return {
                "title": row["name"],
                "uid": row["uid"],
                "order": row["order"],
                "content": numbered,
            }

        world_payload, world_timing = _measure(
            read_world_entry, warmups=warmups, iterations=iterations
        )
        world_read = {
            "selection": "longest_enabled_entry",
            "business_selects": 2,
            "timing": world_timing,
            "output_size": _json_size(world_payload),
        }

    character_list: dict[str, Any] | None = None
    character_read: dict[str, Any] | None = None
    if project_id is not None:
        def list_characters() -> dict[str, Any]:
            rows = connection.execute(
                """
                SELECT *
                FROM characters
                WHERE project_id = ?
                ORDER BY is_favorited DESC, updated_at DESC
                """,
                (project_id,),
            ).fetchall()
            return {"characters": [{"name": row["name"]} for row in rows]}

        character_payload, character_timing = _measure(
            list_characters, warmups=warmups, iterations=iterations
        )
        character_list = {
            "business_selects": 1,
            "timing": character_timing,
            "output_size": _json_size(character_payload),
        }
        target = connection.execute(
            """
            SELECT name
            FROM characters
            WHERE project_id = ?
            ORDER BY length(description) DESC, id ASC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if target is not None:
            target_name = target["name"]

            def read_character() -> dict[str, Any]:
                rows = connection.execute(
                    "SELECT * FROM characters WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                matches = [row for row in rows if row["name"] == target_name]
                if len(matches) != 1:
                    raise RuntimeError("baseline character target is no longer unique")
                row = matches[0]
                numbered = "\n".join(
                    f"{number}|{line}"
                    for number, line in enumerate(
                        (row["description"] or "").splitlines(), start=1
                    )
                )
                return {"name": row["name"], "description": numbered}

            character_payload, character_timing = _measure(
                read_character, warmups=warmups, iterations=iterations
            )
            character_read = {
                "selection": "longest_character",
                "business_selects": 1,
                "timing": character_timing,
                "output_size": _json_size(character_payload),
            }

    return {
        "world_list": {
            "business_selects": 2,
            "timing": world_list_timing,
            "output_size": _json_size(world_list),
        },
        "world_single_read": world_read,
        "character_list": character_list,
        "character_single_read": character_read,
    }


def capture_baseline(
    database: Path,
    *,
    world_info_id: str | None = None,
    warmups: int = 10,
    iterations: int = 100,
) -> dict[str, Any]:
    connection = connect_read_only(database)
    try:
        snapshot = capture_world_book_snapshot(
            connection, world_info_id=world_info_id
        )
        legacy = capture_legacy_baseline(
            connection,
            world_info_id=snapshot["world_info_id"],
            project_id=snapshot["project_id"],
            warmups=warmups,
            iterations=iterations,
        )
    finally:
        connection.close()
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "database_bytes": database.stat().st_size,
        },
        "world_book": snapshot,
        "legacy": legacy,
    }


def fixture_summary(
    characters: Sequence[GeneratedCharacter], *, seed: int = DEFAULT_SEED
) -> dict[str, Any]:
    lengths = [len(character.description) for character in characters]
    alias_counts = [len(character.aliases) for character in characters]
    return {
        "seed": seed,
        "count": len(characters),
        "description_median_chars": int(statistics.median(lengths)) if lengths else 0,
        "description_p95_chars_nearest_rank": _nearest_rank(lengths, 0.95),
        "description_max_chars": max(lengths, default=0),
        "alias_min": min(alias_counts, default=0),
        "alias_max": max(alias_counts, default=0),
        "digest": fixture_digest(characters),
    }


def _write_json(path: Path | None, payload: object) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path is None:
        print(serialized)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate deterministic character JSONL")
    generate.add_argument("--project-id", required=True)
    generate.add_argument("--count", type=int, default=STRESS_CHARACTER_COUNT)
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    generate.add_argument("--output", type=Path)

    capture = subparsers.add_parser("capture", help="capture a read-only legacy baseline")
    capture.add_argument("--database", type=Path, default=Path("data/openfic.db"))
    capture.add_argument("--world-info-id")
    capture.add_argument("--warmups", type=int, default=10)
    capture.add_argument("--iterations", type=int, default=100)
    capture.add_argument("--output", type=Path)

    backup = subparsers.add_parser("backup", help="create a consistent SQLite test copy")
    backup.add_argument("--source", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)

    prepare = subparsers.add_parser(
        "prepare", help="back up a database and insert deterministic legacy characters"
    )
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--destination", type=Path, required=True)
    prepare.add_argument("--project-id", required=True)
    prepare.add_argument("--count", type=int, default=REGULAR_CHARACTER_COUNT)
    prepare.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "generate":
        characters = list(
            generate_characters(args.project_id, count=args.count, seed=args.seed)
        )
        if args.output is not None:
            write_fixture_jsonl(args.output, characters)
        _write_json(None, fixture_summary(characters, seed=args.seed))
        return 0
    if args.command == "capture":
        _write_json(
            args.output,
            capture_baseline(
                args.database,
                world_info_id=args.world_info_id,
                warmups=args.warmups,
                iterations=args.iterations,
            ),
        )
        return 0
    if args.command == "backup":
        backup_database(args.source, args.destination)
        return 0
    if args.command == "prepare":
        _write_json(
            None,
            prepare_fixture_database(
                args.source,
                args.destination,
                project_id=args.project_id,
                count=args.count,
                seed=args.seed,
            ),
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
