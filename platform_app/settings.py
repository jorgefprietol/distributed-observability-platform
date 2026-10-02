import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service: str
    token: str
    inventory_url: str = "http://inventory:8080"
    provisioning_url: str = "http://provisioning:8080"
    database: str = "/data/activations.db"
    log_dir: str = "/logs"
    fault_injection: bool = False
    telemetry: bool = True

    @classmethod
    def from_env(cls):
        settings = cls(
            service=os.getenv("SERVICE_NAME", "activation-api"),
            token=os.getenv("API_TOKEN", ""),
            inventory_url=os.getenv("INVENTORY_URL", "http://inventory:8080"),
            provisioning_url=os.getenv("PROVISIONING_URL", "http://provisioning:8080"),
            database=os.getenv("DATABASE_PATH", "/data/activations.db"),
            log_dir=os.getenv("LOG_DIR", "/logs"),
            fault_injection=os.getenv("ENABLE_FAULT_INJECTION", "false").lower() == "true",
        )
        if len(settings.token) < 32:
            raise ValueError("API_TOKEN must contain at least 32 characters")
        if settings.service not in {"activation-api", "inventory", "provisioning"}:
            raise ValueError("Unknown SERVICE_NAME")
        return settings
