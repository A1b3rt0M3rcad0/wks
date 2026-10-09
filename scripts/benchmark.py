"""Reproducible scoped FTS benchmark in a disposable database, never the app database."""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, text

from wks.bootstrap import build
from wks.cli import provision
from wks.domain.models import new_id
from wks.settings import Settings
from wks.worker import Worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=int, default=100000)
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--output", default="docs/evidence/benchmark.json")
    args = parser.parse_args()
    base = Settings().database_url.rsplit("/", 1)[0]
    name = "wks_benchmark_" + new_id().replace("-", "")
    admin = create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base + "/" + name
    subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        env=os.environ | {"WKS_DATABASE_URL": url},
        check=True,
    )
    with TemporaryDirectory() as temp:
        service, engine = build(
            Settings(
                database_url=url,
                storage_path=Path(temp) / "objects",
                cursor_secret="benchmark-only-key",
            )
        )
        subjects, sources, ingestion = [], [], []
        for i in range(2):
            token_file = Path(temp) / f"token-{i}"
            provision(service, f"benchmark-{i}", "client", token_file)
            p = service.authenticate(token_file.read_text())
            ns = service.namespace_create(p, "namespace", {"title": f"Benchmark tenant {i}"})["id"]
            for sample in range(10):
                started = time.perf_counter()
                receipt = service.register(
                    p,
                    f"source-{sample}",
                    {
                        "namespace_id": ns,
                        "kind": "text",
                        "title": "Electrical reference",
                        "text": "Resistências em paralelo: tensão e corrente elétrica.",
                    },
                )
                Worker(service).run_once()
                assert service.status(p, receipt["operation_id"])["published_representation_id"]
                ingestion.append((time.perf_counter() - started) * 1000)
            rid = service.status(p, receipt["operation_id"])["published_representation_id"]
            subjects.append(p)
            sources.append((ns, rid))
        value = "Resistências em paralelo: tensão e corrente elétrica."
        with engine.begin() as c:
            c.execute(text("DELETE FROM search_segments"))
            for ns, rid in sources:
                c.execute(
                    text("""INSERT INTO search_segments
                 (id,created_at,namespace_id,representation_id,ordinal,block_refs,asset_refs,text,origin_kind,locale,locator,checksum,search_vector)
                 SELECT md5(:ns || ':' || i::text)::uuid::text,now(),:ns,:rid,i,'[]'::jsonb,'[]'::jsonb,
                    :value,'native_text','pt',jsonb_build_object('kind','synthetic','value',i),:sha,
                    to_tsvector('wks_portuguese',:value) FROM generate_series(1,:count) i"""),
                    {
                        "ns": ns,
                        "rid": rid,
                        "value": value,
                        "sha": hashlib.sha256(value.encode()).hexdigest(),
                        "count": args.segments // 2,
                    },
                )
            c.execute(text("ANALYZE search_segments"))
        measurements = []
        for i in range(args.requests + 5):
            t = time.perf_counter()
            result = service.search(
                subjects[i % 2], {"query": "resistência paralelo", "page_size": 20}
            )
            elapsed = (time.perf_counter() - t) * 1000
            assert result["items"] and all(item["source_id"] for item in result["items"])
            if i >= 5:
                measurements.append(elapsed)
        report = {
            "corpus": "synthetic lexical segments; extraction is not benchmarked",
            "segments": args.segments // 2 * 2,
            "tenants": 2,
            "requests": args.requests,
            "warmups": 5,
            "cpu_count": os.cpu_count(),
            "platform": platform.platform(),
            "postgres": None,
            "search_ms": {
                "median": statistics.median(measurements),
                "p95": sorted(measurements)[int(0.95 * (len(measurements) - 1))],
                "min": min(measurements),
                "max": max(measurements),
            },
            "ingestion": {
                "corpus": "20 short UTF-8 text sources; register through extraction and publication",
                "transport": "shared application service, not HTTP upload latency",
                "samples": len(ingestion),
                "median_ms": statistics.median(ingestion),
                "p95_ms": sorted(ingestion)[int(0.95 * (len(ingestion) - 1))],
                "max_ms": max(ingestion),
            },
        }
        with engine.connect() as c:
            report["postgres"] = c.scalar(text("SHOW server_version"))
            plan = (
                c.execute(
                    text(
                        "EXPLAIN SELECT id FROM search_segments WHERE search_vector @@ websearch_to_tsquery('wks_portuguese','resistência paralelo')"
                    )
                )
                .scalars()
                .all()
            )
            report["explain"] = plan
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["search_ms"]))
        engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


if __name__ == "__main__":
    main()
