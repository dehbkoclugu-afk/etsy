from __future__ import annotations

from calendar import Calendar
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pinforge.application.exporter import BundleExporter
from pinforge.clock import utc_now
from pinforge.domain.models import PinStatus, SourceProduct
from pinforge.growth.service import GrowthService
from pinforge.integrations.http import ApiError
from pinforge.runtime import PinForgeRuntime
from pinforge.scheduling import SchedulePlanner
from pinforge.security import SecretStoreError


class CalendarDay(QListWidget):
    moved = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.day: date | None = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMinimumHeight(90)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        source = event.source()
        item = source.currentItem() if isinstance(source, QListWidget) else None
        draft_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if draft_id and self.day:
            self.moved.emit(str(draft_id), self.day.isoformat())
        event.ignore()


class GrowthPage(QWidget):
    def __init__(self, runtime: PinForgeRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.growth = GrowthService(
            runtime.repository, time_zone=runtime.settings.time_zone
        )
        today = date.today()
        self.month = date(today.year, today.month, 1)
        self.day_lists: list[CalendarDay] = []
        self.setLayout(self._build())
        self.refresh()

    def _build(self) -> QVBoxLayout:
        root = QVBoxLayout()
        root.setContentsMargins(0, 18, 0, 0)
        actions = QHBoxLayout()
        for text, slot in (
            ("Pinterest metriklerini al", self._sync_metrics),
            ("Metrik CSV içe aktar", self._import_metrics),
            ("Trend CSV içe aktar", self._import_trends),
            ("Takvimi otomatik doldur", self._auto_fill),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch()
        root.addLayout(actions)

        body = QGridLayout()
        body.addWidget(self._insights_card(), 0, 0)
        body.addWidget(self._seo_card(), 0, 1)
        body.addWidget(self._experiment_card(), 1, 0)
        body.addWidget(self._calendar_card(), 1, 1)
        body.setRowStretch(1, 1)
        body.setColumnStretch(0, 1)
        body.setColumnStretch(1, 2)
        root.addLayout(body, 1)
        return root

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("editorPanel")
        layout = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return card, layout

    def _insights_card(self) -> QFrame:
        card, layout = self._card("Performans öğrenmesi")
        self.dimension = QComboBox()
        self.dimension.addItem("Şablon", "template")
        self.dimension.addItem("Saat", "hour")
        self.dimension.addItem("Haftanın günü", "weekday")
        self.dimension.currentIndexChanged.connect(self._refresh_insights)
        layout.addWidget(self.dimension)
        self.insights = QTableWidget(0, 4)
        self.insights.setHorizontalHeaderLabels(
            ("Değer", "Gösterim", "Etkileşim", "Puan")
        )
        layout.addWidget(self.insights)
        return card

    def _seo_card(self) -> QFrame:
        card, layout = self._card("Trend ve SEO asistanı")
        row = QHBoxLayout()
        self.seo_product = QComboBox()
        row.addWidget(self.seo_product, 1)
        button = QPushButton("Önerileri hesapla")
        button.clicked.connect(self._refresh_seo)
        row.addWidget(button)
        layout.addLayout(row)
        self.seo = QTableWidget(0, 3)
        self.seo.setHorizontalHeaderLabels(("Kelime", "Puan", "Neden"))
        layout.addWidget(self.seo)
        return card

    def _experiment_card(self) -> QFrame:
        card, layout = self._card("Otomatik A/B testi")
        self.experiment_product = QComboBox()
        layout.addWidget(self.experiment_product)
        create = QPushButton("İki yaratıcı varyant oluştur")
        create.clicked.connect(self._create_experiment)
        layout.addWidget(create)
        self.experiments = QTableWidget(0, 4)
        self.experiments.setHorizontalHeaderLabels(("Ad", "Durum", "Kazanan", "Kimlik"))
        layout.addWidget(self.experiments)
        winner = QPushButton("Seçili deneyin kazananını hesapla")
        winner.clicked.connect(self._winner)
        layout.addWidget(winner)
        return card

    def _calendar_card(self) -> QFrame:
        card, layout = self._card("İçerik takvimi")
        navigation = QHBoxLayout()
        previous = QPushButton("‹")
        previous.clicked.connect(lambda: self._change_month(-1))
        self.month_label = QLabel()
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        following = QPushButton("›")
        following.clicked.connect(lambda: self._change_month(1))
        navigation.addWidget(previous)
        navigation.addWidget(self.month_label, 1)
        navigation.addWidget(following)
        layout.addLayout(navigation)
        grid = QGridLayout()
        for column, label in enumerate(
            ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
        ):
            grid.addWidget(QLabel(label), 0, column)
        for index in range(42):
            day_list = CalendarDay()
            day_list.moved.connect(self._move_draft)
            self.day_lists.append(day_list)
            grid.addWidget(day_list, index // 7 + 1, index % 7)
        layout.addLayout(grid, 1)
        return card

    def refresh(self) -> None:
        self.growth = GrowthService(
            self.runtime.repository, time_zone=self.runtime.settings.time_zone
        )
        products = self.runtime.repository.list_products()
        for combo in (self.seo_product, self.experiment_product):
            selected = combo.currentData()
            combo.clear()
            for product in products:
                combo.addItem(product.title, product.id)
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
        self._refresh_insights()
        self._refresh_experiments()
        self._refresh_calendar()

    def _sync_metrics(self) -> None:
        try:
            count, issues = self.growth.sync_pinterest(self.runtime.pinterest_client())
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Metrikler alınamadı", str(exc))
            return
        self.refresh()
        QMessageBox.information(
            self, "Metrikler", f"{count} pin güncellendi, {len(issues)} sorun"
        )

    def _import_metrics(self) -> None:
        self._import_csv("Metrik CSV seç", self.growth.import_metrics_csv)

    def _import_trends(self) -> None:
        self._import_csv("Trend CSV seç", self.growth.import_trends_csv)

    def _import_csv(self, title: str, importer) -> None:  # noqa: ANN001
        path, _ = QFileDialog.getOpenFileName(self, title, "", "CSV (*.csv)")
        if not path:
            return
        try:
            count = importer(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "İçe aktarma başarısız", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "İçe aktarma", f"{count} satır kaydedildi")

    def _refresh_insights(self) -> None:
        values = self.growth.insights(str(self.dimension.currentData()))
        self.insights.setRowCount(len(values))
        for row, value in enumerate(values):
            for column, text in enumerate(
                (
                    value.value,
                    value.impressions,
                    value.engagements,
                    f"{value.score:.4f}",
                )
            ):
                self.insights.setItem(row, column, QTableWidgetItem(str(text)))

    def _product(self, combo: QComboBox) -> SourceProduct | None:
        product_id = combo.currentData()
        return next(
            (
                item
                for item in self.runtime.repository.list_products()
                if item.id == product_id
            ),
            None,
        )

    def _refresh_seo(self) -> None:
        product = self._product(self.seo_product)
        values = self.growth.seo_suggestions(product) if product else ()
        self.seo.setRowCount(len(values))
        for row, value in enumerate(values):
            for column, text in enumerate(
                (value.term, f"{value.score:.2f}", value.reason)
            ):
                self.seo.setItem(row, column, QTableWidgetItem(text))

    def _create_experiment(self) -> None:
        product = self._product(self.experiment_product)
        if not product:
            QMessageBox.information(self, "Ürün seçin", "Önce bir ürün seçin.")
            return
        folder = QFileDialog.getExistingDirectory(self, "A/B çıktı klasörünü seç")
        if not folder:
            return
        destination = (
            Path(folder) / f"ab-{product.id}-{utc_now().strftime('%Y%m%d%H%M%S')}"
        )
        try:
            result = BundleExporter().export_product(
                product, ("mockup_hero", "text_overlay"), destination
            )
            self.runtime.repository.save_drafts(result.drafts)
            experiment_id = self.growth.create_experiment(
                product.id, f"{product.title} A/B", result.drafts
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "A/B testi oluşturulamadı", str(exc))
            return
        self.refresh()
        QMessageBox.information(
            self, "A/B testi", f"Deney oluşturuldu: {experiment_id}"
        )

    def _refresh_experiments(self) -> None:
        rows = self.runtime.repository.list_experiments()
        self.experiments.setRowCount(len(rows))
        for row, record in enumerate(rows):
            values = (
                record["name"],
                record["status"],
                record["winner_variant"] or "-",
                record["id"],
            )
            for column, value in enumerate(values):
                self.experiments.setItem(row, column, QTableWidgetItem(str(value)))

    def _winner(self) -> None:
        row = self.experiments.currentRow()
        if row < 0:
            return
        experiment_id = self.experiments.item(row, 3).text()
        try:
            result = self.growth.experiment_result(experiment_id, finalize=True)
        except ValueError as exc:
            QMessageBox.critical(self, "Kazanan hesaplanamadı", str(exc))
            return
        self.refresh()
        message = (
            f"Kazanan: {result.winner_label}"
            if result.winner_label
            else "Henüz iki varyantta yeterli gösterim yok."
        )
        QMessageBox.information(self, "A/B sonucu", message)

    def _auto_fill(self) -> None:
        drafts = self.runtime.repository.list_drafts(PinStatus.READY)
        if not drafts:
            return
        products = {item.id: item for item in self.runtime.repository.list_products()}
        try:
            slots = SchedulePlanner.next_slots(
                utc_now(),
                len(drafts),
                self.runtime.settings.schedule_slots,
                max_daily=self.runtime.settings.max_daily_pins,
                time_zone=self.runtime.settings.time_zone,
            )
            values = []
            validated_boards: set[str] = set()
            for draft, scheduled_at in zip(drafts, slots, strict=True):
                product = products.get(draft.product_id)
                board = self.runtime.board_for_vertical(
                    product.vertical if product else ""
                )
                if not board:
                    raise ValueError(f"{draft.product_id} için board seçilmedi")
                if board not in validated_boards:
                    self.runtime.validate_board_id(board)
                    validated_boards.add(board)
                values.append((draft.id, scheduled_at, board))
            self.runtime.repository.schedule_many(values)
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Takvim doldurulamadı", str(exc))
            return
        self._refresh_calendar()

    def _refresh_calendar(self) -> None:
        self.month_label.setText(self.month.strftime("%B %Y"))
        weeks = Calendar(firstweekday=0).monthdatescalendar(
            self.month.year, self.month.month
        )
        days = [day for week in weeks for day in week]
        while len(days) < 42:
            days.append(days[-1].fromordinal(days[-1].toordinal() + 1))
        zone = ZoneInfo(self.runtime.settings.time_zone)
        grouped: dict[date, list] = {}
        for draft in self.runtime.repository.list_drafts():
            if draft.scheduled_at:
                grouped.setdefault(
                    draft.scheduled_at.astimezone(zone).date(), []
                ).append(draft)
        for widget, day in zip(self.day_lists, days, strict=True):
            widget.clear()
            widget.day = day
            widget.setToolTip(day.isoformat())
            widget.setStyleSheet(
                "" if day.month == self.month.month else "color: #8A918D;"
            )
            header = QListWidgetItem(str(day.day))
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(header)
            for draft in grouped.get(day, []):
                item = QListWidgetItem(
                    f"{draft.scheduled_at.astimezone(zone):%H:%M} {draft.title[:24]}"
                )
                item.setData(Qt.ItemDataRole.UserRole, draft.id)
                widget.addItem(item)

    def _move_draft(self, draft_id: str, day_value: str) -> None:
        try:
            draft = self.runtime.repository.get_draft(draft_id)
            if not draft.scheduled_at:
                raise ValueError("Taslak zamanlanmamış")
            zone = ZoneInfo(self.runtime.settings.time_zone)
            local_time = (
                draft.scheduled_at.astimezone(zone).timetz().replace(tzinfo=None)
            )
            target_day = date.fromisoformat(day_value)
            scheduled_at = SchedulePlanner.next_slots(
                datetime.combine(target_day, time.min, zone),
                1,
                (f"{local_time.hour:02d}:{local_time.minute:02d}",),
                max_daily=1,
                time_zone=self.runtime.settings.time_zone,
            )[0]
            self.runtime.repository.reschedule_queued(draft_id, scheduled_at)
        except ValueError as exc:
            QMessageBox.critical(self, "Pin taşınamadı", str(exc))
        self._refresh_calendar()

    def _change_month(self, delta: int) -> None:
        year = self.month.year + (self.month.month - 1 + delta) // 12
        month = (self.month.month - 1 + delta) % 12 + 1
        self.month = date(year, month, 1)
        self._refresh_calendar()
