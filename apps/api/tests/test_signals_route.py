"""Coverage test for the /api/signals stub route.

Returns [] until the ingestion / ML pipeline starts populating the
underlying table with the rich shape the UI table expects.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_signals_returns_empty_list_until_pipeline_populates(client: TestClient) -> None:
    assert client.get("/api/signals").json() == []
