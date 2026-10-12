"""Evaluate explicit qrels against a WKS public API. Scores describe only supplied data."""

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path

from wks_sdk import Client


def evaluate(client, cases, k=10):
    if not cases:
        raise ValueError("At least one query with explicit relevance judgments is required")
    results = []
    for case in cases:
        judged = case["relevance"]
        if not judged or any(not isinstance(score, int) or score < 0 for score in judged.values()):
            raise ValueError(
                "Every query requires explicit nonnegative integer relevance judgments"
            )
        started = time.perf_counter()
        page = client.call(
            "POST",
            "/v1/search",
            {
                "query": case["query"],
                "page_size": k,
                "mode": case.get("mode", "lexical"),
                "language": case.get("language", "auto"),
                "filters": case.get("filters", {}),
            },
        )
        elapsed = time.perf_counter() - started
        hits = list(dict.fromkeys(hit["source_id"] for hit in page["items"]))[:k]
        positives = {identity for identity, grade in judged.items() if grade > 0}
        recall = len(set(hits) & positives) / len(positives) if positives else 0
        dcg = sum(
            (2 ** judged.get(identity, 0) - 1) / math.log2(index + 2)
            for index, identity in enumerate(hits)
        )
        ideal = sum(
            (2**grade - 1) / math.log2(index + 2)
            for index, grade in enumerate(sorted(judged.values(), reverse=True)[:k])
        )
        results.append(
            {
                "query_id": case["id"],
                "recall_at_k": recall,
                "ndcg_at_k": dcg / ideal if ideal else 0,
                "seconds": elapsed,
                "next_cursor": bool(page.get("next_cursor")),
            }
        )
    return {
        "query_count": len(results),
        "k": k,
        "mean_recall_at_k": statistics.mean(item["recall_at_k"] for item in results),
        "mean_ndcg_at_k": statistics.mean(item["ndcg_at_k"] for item in results),
        "mean_seconds": statistics.mean(item["seconds"] for item in results),
        "queries": results,
        "scope": "supplied qrels only; no external quality or capacity certification",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    token = os.environ.get("WKS_EVALUATION_TOKEN")
    if not token:
        parser.error("Configure WKS_EVALUATION_TOKEN securely")
    value = json.loads(Path(args.qrels).read_text())
    report = evaluate(Client(args.endpoint, token), value["queries"])
    report["dataset"] = value.get("dataset", "unspecified")
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
