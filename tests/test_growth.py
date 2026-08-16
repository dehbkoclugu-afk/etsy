from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3

import httpx
import pytest

from pinforge.application.exporter import BundleExporter
from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinStatus
from pinforge.growth.models import MetricSnapshot
from pinforge.growth.service import GrowthService
from pinforge.importers.folder import FolderImporter
from pinforge.integrations.http import JsonHttpClient
from pinforge.integrations.pinterest import PinterestClient
from pinforge.runtime import PINTEREST_TOKEN, PinForgeRuntime
from pinforge.security import MemorySecretStore


def _growth_repository(product_folder: Path, tmp_path: Path):  # noqa: ANN202
    product = FolderImporter.load(product_folder).products[0]
    exported = BundleExporter().export_product(
        product, ("mockup_hero", "text_overlay"), tmp_path / "out"
    )
    repository = PinRepository(tmp_path / "pinforge.db")
    repository.save_product(product)
    drafts = tuple(
        replace(
            draft,
            status=PinStatus.PUBLISHED,
            pinterest_pin_id=f"pin-{index}",
            published_at=datetime(2026, 8, index + 1, 9 + index, tzinfo=UTC),
        )
        for index, draft in enumerate(exported.drafts)
    )
    repository.save_drafts(drafts)
    return product, drafts, repository


def test_metrics_upsert_insights_and_experiment_winner(
    product_folder: Path, tmp_path: Path
) -> None:
    product, drafts, repository = _growth_repository(product_folder, tmp_path)
    with repository:
        growth = GrowthService(repository)
        repository.save_metrics(
            (
                MetricSnapshot("pin-0", date(2026, 8, 2), 1000, 10, 5, 5),
                MetricSnapshot("pin-1", date(2026, 8, 2), 1000, 1, 1, 0),
            )
        )
        repository.save_metrics(
            (MetricSnapshot("pin-0", date(2026, 8, 2), 1200, 12, 6, 6),)
        )
        assert repository.list_metrics("pin-0")[0].impressions == 1200
        assert growth.insights()[0].value == "mockup_hero"
        assert growth.insights()[0].impressions == 1200

        experiment_id = growth.create_experiment(product.id, "Creative", drafts)
        result = growth.experiment_result(experiment_id, finalize=True)
        assert result.winner_label == "A"
        assert result.status == "completed"


def test_csv_imports_are_atomic_and_feed_seo(
    product_folder: Path, tmp_path: Path
) -> None:
    product, _, repository = _growth_repository(product_folder, tmp_path)
    with repository:
        growth = GrowthService(repository)
        trends = tmp_path / "trends.csv"
        trends.write_text(
            "term,score,vertical,date\nwelcome,90,airbnb,2026-08-02\nhost guide,75,airbnb,2026-08-02\n",
            encoding="utf-8",
        )
        assert growth.import_trends_csv(trends) == 2
        suggestions = growth.seo_suggestions(product)
        assert suggestions[0].term in {"welcome", "host guide"}

        invalid = tmp_path / "metrics.csv"
        invalid.write_text(
            "pin_id,date,impressions,saves,pin_clicks,outbound_clicks\n"
            "pin-0,2026-08-02,100,2,1,1\n"
            "pin-1,2026-08-02,-1,0,0,0\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="satır 2"):
            growth.import_metrics_csv(invalid)
        assert repository.list_metrics() == ()


def test_profiles_isolate_database_settings_and_secrets(
    product_folder: Path, tmp_path: Path
) -> None:
    secrets = MemorySecretStore({PINTEREST_TOKEN: "default-token"})
    runtime = PinForgeRuntime(tmp_path / "data", secrets)
    product = FolderImporter.load(product_folder).products[0]
    runtime.repository.save_product(product)
    profile = runtime.create_profile("İkinci Mağaza")
    runtime.switch_profile(profile.id)
    assert runtime.repository.list_products() == ()
    assert runtime.get_secret(PINTEREST_TOKEN) is None
    runtime.set_secret(PINTEREST_TOKEN, "second-token")
    runtime.switch_profile("default")
    assert runtime.repository.list_products()[0].id == product.id
    assert runtime.get_secret(PINTEREST_TOKEN) == "default-token"
    runtime.close()


def test_pinterest_analytics_normalizes_nested_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/pins/123/analytics")
        return httpx.Response(
            200,
            json={
                "all": {
                    "summary": {
                        "IMPRESSION": 1200,
                        "SAVE": {"value": 30},
                        "PIN_CLICK": 10,
                        "OUTBOUND_CLICK": 8,
                    }
                }
            },
        )

    client = PinterestClient(
        "token",
        http=JsonHttpClient(httpx.Client(transport=httpx.MockTransport(handler))),
    )
    metrics = client.pin_analytics("123", date(2026, 8, 1), date(2026, 8, 2))
    assert metrics == {
        "IMPRESSION": 1200,
        "SAVE": 30,
        "PIN_CLICK": 10,
        "OUTBOUND_CLICK": 8,
    }


def test_calendar_move_preserves_queued_state(
    product_folder: Path, tmp_path: Path
) -> None:
    product = FolderImporter.load(product_folder).products[0]
    exported = BundleExporter().export_product(
        product, ("mockup_hero",), tmp_path / "out"
    )
    with PinRepository(tmp_path / "pinforge.db") as repository:
        repository.save_product(product)
        repository.save_drafts(exported.drafts)
        start = datetime(2026, 8, 2, 9, tzinfo=UTC)
        repository.schedule(exported.drafts[0].id, start, "board")
        repository.reschedule_queued(exported.drafts[0].id, start + timedelta(days=1))
        moved = repository.get_draft(exported.drafts[0].id)
        assert moved.status is PinStatus.QUEUED
        assert moved.scheduled_at == start + timedelta(days=1)


def test_v3_database_migrates_growth_tables(tmp_path: Path) -> None:
    database = tmp_path / "legacy-v3.db"
    with PinRepository(database):
        pass
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        DROP TABLE experiment_variants;
        DROP TABLE experiments;
        DROP TABLE pin_metrics;
        DROP TABLE trend_terms;
        PRAGMA user_version = 3;
        """
    )
    connection.close()
    with PinRepository(database) as repository:
        assert repository.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert repository.list_metrics() == ()
        assert repository.list_experiments() == ()
    assert database.with_name("legacy-v3.db.v3.bak").is_file()
