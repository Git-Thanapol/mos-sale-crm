"""Measures real p50/p95 render time per route against whatever data is
currently in the DB (e.g. after the real historical import), using Django's
test Client authenticated via force_login. This is the tests/perf/ suite
docs/RUNBOOK.md promises but that was never actually built in an earlier
phase - see conversation. Not wired into pytest; run directly:

    docker compose exec web python manage.py bench_pages --email <user@example.com>

Uses the test Client (in-process WSGI call) rather than real HTTP, so
numbers exclude network/nginx/gunicorn overhead and measure ORM+template
render cost only - the part this rebuild's index/schema work targets.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from crm.accounts.models import User

ROUTES = [
    ("/dashboard/", "dashboard"),
    ("/customers/", "customers"),
    ("/followup/", "followup"),
    ("/team-sales/", "team_sales"),
]

TARGETS_MS = {
    "followup": 150,
    "customers": 200,
    "dashboard": 250,
}


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


class Command(BaseCommand):
    help = "Bench p50/p95 render time for the main list/report routes against live data."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="User to force_login as")
        parser.add_argument("--runs", type=int, default=20, help="Requests per route (default 20)")
        parser.add_argument("--warmup", type=int, default=3, help="Untimed warmup requests (default 3)")

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=options["email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"no user with email {options['email']!r}") from exc

        client = Client(SERVER_NAME="localhost")
        client.force_login(user)
        runs = options["runs"]
        warmup = options["warmup"]

        results = {}
        for path, key in ROUTES:
            for _ in range(warmup):
                client.get(path)

            samples = []
            for _ in range(runs):
                start = time.perf_counter()
                response = client.get(path)
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code != 200:
                    raise CommandError(f"{path} returned {response.status_code}, expected 200")
                samples.append(elapsed_ms)

            samples.sort()
            p50 = _percentile(samples, 0.50)
            p95 = _percentile(samples, 0.95)
            results[key] = {"p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "n": runs}

        self.stdout.write(self.style.SUCCESS(f"user={options['email']} runs={runs}"))
        for key, stats in results.items():
            target = TARGETS_MS.get(key)
            target_note = f" (target p95 < {target}ms)" if target else ""
            flag = ""
            if target and stats["p95_ms"] >= target:
                flag = "  [OVER TARGET]"
            self.stdout.write(
                f"  {key:12s} p50={stats['p50_ms']:>8.1f}ms  p95={stats['p95_ms']:>8.1f}ms{target_note}{flag}"
            )
