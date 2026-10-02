"""Generate per-installation credentials; never overwrite an existing environment."""

import argparse
import secrets
from contextlib import suppress
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--fault-injection", action="store_true")
args = parser.parse_args()
path = Path(".env")
if path.exists():
    raise SystemExit(".env already exists; credentials preserved")
names = [
    "API_TOKEN",
    "ELASTIC_PASSWORD",
    "KIBANA_PASSWORD",
    "INGEST_PASSWORD",
    "KIBANA_ENCRYPTION_KEY",
    "APM_SECRET_TOKEN",
    "OTEL_TOKEN",
]
path.write_text(
    "".join(f"{name}={secrets.token_hex(32)}\n" for name in names)
    + f"ENABLE_FAULT_INJECTION={str(args.fault_injection).lower()}\n",
    encoding="utf-8",
)
with suppress(OSError):
    path.chmod(0o600)
print("Created .env with unique credentials")
