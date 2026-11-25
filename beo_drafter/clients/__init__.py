from beo_drafter.clients.base import ModelClient
from beo_drafter.clients.mock import MockModelClient
from beo_drafter.clients.ollama import OllamaCloudClient

CLIENTS = {
    "mock": MockModelClient,
    "ollama": OllamaCloudClient,
}


def get_client(name: str) -> ModelClient:
    key = name.strip().lower()
    if key not in CLIENTS:
        known = ", ".join(sorted(CLIENTS))
        raise ValueError(f"unknown client {name!r}; expected one of: {known}")
    return CLIENTS[key]()
