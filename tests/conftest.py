"""Shared fixtures. No real network: Elexon is mocked with respx and recorded fixtures."""

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from django.core.cache import cache
from django.core.management import call_command

from apps.market_data.models import GenerationActual, IngestionRun, WindForecast

FIXTURES = Path(__file__).parent / "fixtures"


def ts(value: str) -> pd.Timestamp:
    """A UTC timestamp from an ISO string ending in Z."""
    return pd.Timestamp(value).tz_convert("UTC")


def load_fixture(name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text())
    return rows


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(scope="session")
def _snapshot_loaded(django_db_setup: None, django_db_blocker: Any) -> None:
    """Load the committed real-data snapshot once per session (outside per-test transactions)."""
    with django_db_blocker.unblock():
        call_command("load_snapshot", stdout=io.StringIO())


@pytest.fixture
def snapshot_db(_snapshot_loaded: None, db: None) -> None:
    """The real January 2026 data, read-only for the test (each test is rolled back)."""


@pytest.fixture
def empty_db(db: None) -> None:
    """An empty market-data schema, whatever earlier tests loaded (rolled back afterwards)."""
    WindForecast.objects.all().delete()
    GenerationActual.objects.all().delete()
    IngestionRun.objects.all().delete()
