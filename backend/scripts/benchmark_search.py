import json
import os
from statistics import mean
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shopflow.settings")

import django

django.setup()

from django.contrib.postgres.search import SearchQuery, SearchRank  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db.models import F, Q  # noqa: E402
from store.models import Product  # noqa: E402

SEARCH_TERMS = [
    "workstation keyboard thunderbolt",
    "nvme encryption SSD",
    "planar studio microphone",
    "rackmount homelab switch",
    "calibrated monitor ultrawide",
    "tactile aluminum developer",
    "portable encrypted backup",
    "wireless blade cooling",
    "balanced xlr acoustic",
    "storage throughput snapshot",
]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int((p / 100.0) * len(ordered)))
    return ordered[idx]


def main():
    if Product.objects.count() < 1000:
        call_command("seed_products", count=1000)

    # Warm query planner & DB buffers
    list(Product.objects.filter(name__icontains="keyboard")[:24])
    list(
        Product.objects.filter(
            search_vector=SearchQuery("keyboard", config="english", search_type="websearch")
        )[:24]
    )

    like_times = []
    fts_times = []
    iterations = 5

    for _ in range(iterations):
        for term in SEARCH_TERMS:
            words = term.split()

            t0 = time.perf_counter()
            like_q = Q()
            for w in words:
                like_q |= Q(name__icontains=w) | Q(description__icontains=w)
            list(Product.objects.filter(like_q, is_active=True)[:24])
            like_times.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            sq = SearchQuery(term, config="english", search_type="websearch")
            list(
                Product.objects.filter(search_vector=sq, is_active=True)
                .annotate(rank=SearchRank(F("search_vector"), sq))
                .order_by("-rank", "id")[:24]
            )
            fts_times.append((time.perf_counter() - t1) * 1000.0)

    like_mean = mean(like_times)
    fts_mean = mean(fts_times)
    reduction_pct = ((like_mean - fts_mean) / like_mean) * 100.0

    print(
        json.dumps(
            {
                "productCount": Product.objects.count(),
                "queriesMeasured": len(like_times),
                "naiveIcontainsMs": {
                    "mean": round(like_mean, 3),
                    "p50": round(percentile(like_times, 50), 3),
                    "p95": round(percentile(like_times, 95), 3),
                },
                "postgresFtsGinMs": {
                    "mean": round(fts_mean, 3),
                    "p50": round(percentile(fts_times, 50), 3),
                    "p95": round(percentile(fts_times, 95), 3),
                },
                "latencyReductionPct": round(reduction_pct, 1),
                "speedupX": round(like_mean / fts_mean, 2),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
