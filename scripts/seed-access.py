"""Synthetic access fixtures for ingestion and quarantine verification."""

from datetime import UTC, datetime
from pathlib import Path

timestamp = datetime.now(UTC).strftime("%d/%b/%Y:%H:%M:%S +0000")
with Path("/logs/synthetic.access").open("a", encoding="utf-8") as target:
    target.write(
        f'8.8.8.8 - - [{timestamp}] "GET /synthetic-check HTTP/1.1" 200 128 '
        '"-" "synthetic-observability-fixture"\n'
    )
    target.write("synthetic-malformed-access-fixture used to verify quarantine processing\n")
print("Synthetic access fixtures appended")
