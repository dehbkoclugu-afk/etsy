from __future__ import annotations

from pathlib import Path
import sqlite3

from pinforge.application.exporter import BundleExporter
from pinforge.data.repository import PinRepository
from pinforge.domain.models import PinStatus
from pinforge.importers.folder import FolderImporter


def test_persists_and_queues_draft(product_folder: Path, tmp_path: Path) -> None:
    product = FolderImporter.load(product_folder).products[0]
    exported = BundleExporter().export_product(
        product, ["mockup_hero"], tmp_path / "out"
    )
    with PinRepository(tmp_path / "pinforge.db") as repository:
        repository.save_product(product)
        repository.save_drafts(exported.drafts)
        draft = repository.list_drafts()[0]
        assert draft.status is PinStatus.READY
        repository.schedule(draft.id, draft.approved_at, "board")
        assert repository.list_drafts(PinStatus.QUEUED)[0].id == draft.id


def test_migrates_v1_database_without_losing_rows(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE products (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, listing_url TEXT NOT NULL,
            price REAL NOT NULL, currency TEXT NOT NULL, vertical TEXT NOT NULL,
            tags_json TEXT NOT NULL, image_paths_json TEXT NOT NULL,
            description TEXT NOT NULL, imported_at TEXT NOT NULL
        );
        CREATE TABLE pin_drafts (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL, template_id TEXT NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL, alt_text TEXT NOT NULL,
            destination_url TEXT NOT NULL, image_path TEXT, scheduled_at TEXT,
            status TEXT NOT NULL, last_error TEXT, attempt_count INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO products VALUES (
            'p', 'Product', 'https://example.test', 1, 'USD', 'test', '[]', '[]', '',
            '2026-01-01T00:00:00'
        );
        INSERT INTO pin_drafts VALUES (
            'd', 'p', 'mockup_hero', 'Title', 'Description', 'Alt',
            'https://example.test', NULL, NULL, 'draft', NULL, 0
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    with PinRepository(database) as repository:
        draft = repository.list_drafts()[0]
        assert draft.id == "d"
        assert draft.board_id is None
        assert repository.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert database.with_name("legacy.db.v1.bak").is_file()
