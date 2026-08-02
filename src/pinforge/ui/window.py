from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import ImageQt
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QCursor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pinforge.application.exporter import BundleExporter
from pinforge.clock import utc_now
from pinforge.application.generator import build_copy
from pinforge.domain.models import PinCopy, PinStatus, SourceProduct
from pinforge.importers.folder import FolderImporter, ManifestError
from pinforge.integrations.http import ApiError
from pinforge.rendering.engine import RenderEngine, RenderError
from pinforge.runtime import PINTEREST_TOKEN, PinForgeRuntime
from pinforge.scheduling import SchedulePlanner, SchedulerService
from pinforge.security import SecretStoreError
from pinforge.ui.settings_page import SettingsPage
from pinforge.ui.growth_page import GrowthPage


class PinForgeWindow(QMainWindow):
    def __init__(self, runtime: PinForgeRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.repository = runtime.repository
        self.engine = RenderEngine()
        self.exporter = BundleExporter(self.engine)
        self.products: tuple[SourceProduct, ...] = ()
        self.current_product: SourceProduct | None = None
        self.template_checks: dict[str, QCheckBox] = {}
        self.generated_copies: dict[str, PinCopy] = {}
        self._setting_copy = False
        self.setWindowTitle("PinForge")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)
        self.setStatusBar(QStatusBar())
        self.setCentralWidget(self._build_root())
        self._refresh_profiles()
        self._load_persisted_products()
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(5 * 60 * 1000)
        self.scheduler_timer.timeout.connect(lambda: self._drain_queue(silent=True))
        self.scheduler_timer.start()
        self.statusBar().showMessage("PinForge hazır")

    def _build_root(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(14)
        layout.addLayout(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_generator_tab(), "Pin üret")
        self.tabs.addTab(self._build_queue_tab(), "Kuyruk")
        self.growth_page = GrowthPage(self.runtime)
        self.tabs.addTab(self.growth_page, "Büyüme")
        self.settings_page = SettingsPage(self.runtime)
        self.settings_page.saved.connect(self._settings_saved)
        self.tabs.addTab(self.settings_page, "Ayarlar")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        return root

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title_group = QVBoxLayout()
        title = QLabel("PinForge")
        title.setObjectName("appTitle")
        subtitle = QLabel("Ürün görsellerinden tutarlı Pinterest pinleri üretin")
        subtitle.setObjectName("muted")
        title_group.addWidget(title)
        title_group.addWidget(subtitle)
        header.addLayout(title_group)
        header.addStretch()
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("Aktif hesap profili")
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        header.addWidget(self.profile_combo)
        profile_button = QPushButton("Yeni profil")
        profile_button.clicked.connect(self._create_profile)
        header.addWidget(profile_button)
        etsy_button = QPushButton("Etsy'den getir")
        etsy_button.setAccessibleName("Etsy ürünlerini içe aktar")
        etsy_button.clicked.connect(self._import_etsy)
        header.addWidget(etsy_button)
        open_button = QPushButton("Ürün klasörü aç")
        open_button.setAccessibleName("Ürün klasörü aç")
        open_button.clicked.connect(self._open_folder)
        header.addWidget(open_button)
        return header

    def _build_generator_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 14, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_editor())
        splitter.addWidget(self._build_preview())
        splitter.setSizes([300, 370, 700])
        layout.addWidget(splitter)
        return container

    def _build_sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sidebar")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        title = QLabel("Ürünler")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.product_list = QListWidget()
        self.product_list.setAccessibleName("İçe aktarılan ürünler")
        self.product_list.currentRowChanged.connect(self._select_product)
        layout.addWidget(self.product_list, 1)
        template_title = QLabel("Şablonlar")
        template_title.setObjectName("sectionTitle")
        layout.addWidget(template_title)
        for template in self.engine.templates.values():
            checkbox = QCheckBox(template.display_name)
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._selection_changed)
            self.template_checks[template.id] = checkbox
            layout.addWidget(checkbox)
        return frame

    def _build_editor(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("editorPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)
        title = QLabel("Pin metni")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        ai_button = QPushButton("AI ile 5 farklı metin üret")
        ai_button.clicked.connect(self._generate_ai_copy)
        layout.addWidget(ai_button)
        layout.addWidget(QLabel("Başlık"))
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(100)
        self.title_input.setAccessibleName("Pinterest başlığı")
        layout.addWidget(self.title_input)
        self.title_counter = QLabel("0 / 100")
        self.title_counter.setObjectName("muted")
        layout.addWidget(self.title_counter)
        layout.addWidget(QLabel("Açıklama"))
        self.description_input = QTextEdit()
        self.description_input.setAccessibleName("Pinterest açıklaması")
        self.description_input.setPlaceholderText(
            "Ürünün faydasını ve kullanım alanını açıklayın"
        )
        layout.addWidget(self.description_input, 1)
        self.description_counter = QLabel("0 / 500")
        self.description_counter.setObjectName("muted")
        layout.addWidget(self.description_counter)
        layout.addWidget(QLabel("Mağaza adı"))
        self.shop_input = QLineEdit(self.runtime.settings.brand_shop_name)
        self.shop_input.setMaxLength(30)
        layout.addWidget(self.shop_input)
        refresh = QPushButton("Önizlemeyi güncelle")
        refresh.clicked.connect(self._refresh_previews)
        layout.addWidget(refresh)
        self.export_button = QPushButton("PNG ve CSV dışa aktar")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        layout.addWidget(self.export_button)
        self.title_input.textChanged.connect(self._copy_edited)
        self.description_input.textChanged.connect(self._copy_edited)
        return frame

    def _build_preview(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_host = QWidget()
        self.preview_grid = QGridLayout(self.preview_host)
        self.preview_grid.setContentsMargins(18, 4, 18, 18)
        self.preview_grid.setSpacing(20)
        self.preview_grid.addWidget(
            self._empty_label("Önizleme için bir ürün seçin"), 0, 0
        )
        scroll.setWidget(self.preview_host)
        return scroll

    def _build_queue_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        controls = QHBoxLayout()
        heading = QLabel("Pin yayın kuyruğu")
        heading.setObjectName("sectionTitle")
        controls.addWidget(heading)
        controls.addStretch()
        schedule_button = QPushButton("Hazırları zamanla")
        schedule_button.clicked.connect(self._schedule_ready)
        controls.addWidget(schedule_button)
        publish_button = QPushButton("Seçileni şimdi yayınla")
        publish_button.clicked.connect(self._publish_selected)
        controls.addWidget(publish_button)
        drain_button = QPushButton("Zamanı gelenleri yayınla")
        drain_button.setObjectName("primaryButton")
        drain_button.clicked.connect(lambda: self._drain_queue(silent=False))
        controls.addWidget(drain_button)
        layout.addLayout(controls)
        self.queue_table = QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels(
            ("Başlık", "Şablon", "Durum", "Tarih", "Board", "Dosya")
        )
        self.queue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.queue_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.queue_table)
        return page

    def _load_persisted_products(self) -> None:
        self._set_products(self.repository.list_products())

    def _set_products(self, products: tuple[SourceProduct, ...]) -> None:
        self.products = products
        self.product_list.clear()
        for product in products:
            item = QListWidgetItem(product.title)
            item.setToolTip(
                f"{product.vertical} | {product.price:g} {product.currency}"
            )
            self.product_list.addItem(item)
        if products:
            self.product_list.setCurrentRow(0)

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ürün klasörünü seç")
        if not folder:
            return
        try:
            result = FolderImporter.load(folder)
            self.repository.save_products(result.products)
        except (ManifestError, OSError) as exc:
            QMessageBox.critical(self, "İçe aktarma başarısız", str(exc))
            return
        self._load_persisted_products()
        message = f"{len(result.products)} ürün içe aktarıldı"
        if result.issues:
            message += f", {len(result.issues)} kayıt atlandı"
        self.statusBar().showMessage(message, 8000)

    def _import_etsy(self) -> None:
        cache = self.runtime.settings.import_cache_directory or str(
            self.runtime.data_directory / "etsy-cache"
        )
        self._busy(True, "Etsy ürünleri alınıyor")
        try:
            products = self.runtime.etsy_client().import_shop(
                self.runtime.settings.etsy_shop_id, cache
            )
            self.repository.save_products(products)
            self.repository.mark_missing_products_inactive(
                product.id for product in products
            )
            self.repository.audit(
                "etsy_imported",
                entity_type="import",
                provider="etsy",
                details={"count": len(products)},
            )
        except (ApiError, OSError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Etsy içe aktarma başarısız", str(exc))
            return
        finally:
            self._busy(False)
        self._load_persisted_products()
        self.statusBar().showMessage(f"{len(products)} Etsy ürünü alındı", 8000)

    def _select_product(self, row: int) -> None:
        if row < 0 or row >= len(self.products):
            self.current_product = None
            self.export_button.setEnabled(False)
            return
        self.current_product = self.products[row]
        self.generated_copies.clear()
        copy = build_copy(self.current_product, "mockup_hero")
        self._set_editor_copy(copy)
        self.export_button.setEnabled(True)
        self._refresh_previews()

    def _selection_changed(self) -> None:
        self.export_button.setEnabled(
            bool(self.current_product and self._selected_templates())
        )

    def _selected_templates(self) -> list[str]:
        return [
            template_id
            for template_id, checkbox in self.template_checks.items()
            if checkbox.isChecked()
        ]

    def _current_copy(self, template_id: str) -> PinCopy:
        if template_id in self.generated_copies:
            return self.generated_copies[template_id]
        if not self.current_product:
            raise ValueError("Ürün seçilmedi")
        return build_copy(
            self.current_product,
            template_id,
            title=self.title_input.text(),
            description=self.description_input.toPlainText(),
        )

    def _generate_ai_copy(self) -> None:
        if not self.current_product:
            QMessageBox.information(self, "Ürün seçin", "Önce bir ürün seçmelisiniz.")
            return
        selected = self._selected_templates()
        if not selected:
            QMessageBox.information(
                self, "Şablon seçin", "En az bir şablon seçmelisiniz."
            )
            return
        self._busy(True, "AI metinleri üretiliyor")
        try:
            self.generated_copies = self.runtime.copy_generator().generate(
                self.current_product,
                selected,
                target_keywords=self.current_product.tags,
            )
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Metin üretilemedi", str(exc))
            return
        finally:
            self._busy(False)
        self._set_editor_copy(self.generated_copies[selected[0]])
        self._refresh_previews()
        self.statusBar().showMessage(f"{len(selected)} farklı metin üretildi", 8000)

    def _set_editor_copy(self, copy: PinCopy) -> None:
        self._setting_copy = True
        self.title_input.setText(copy.title)
        self.description_input.setPlainText(copy.description)
        self._setting_copy = False
        self._update_counters()

    def _copy_edited(self) -> None:
        self._update_counters()
        if not self._setting_copy:
            self.generated_copies.clear()

    def _refresh_previews(self) -> None:
        while self.preview_grid.count():
            item = self.preview_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.current_product:
            self.preview_grid.addWidget(
                self._empty_label("Önizleme için bir ürün seçin"), 0, 0
            )
            return
        selected = self._selected_templates()
        if not selected:
            self.preview_grid.addWidget(
                self._empty_label("En az bir şablon seçin"), 0, 0
            )
            return
        self._busy(True, "Önizlemeler hazırlanıyor")
        try:
            for index, template_id in enumerate(selected):
                self.preview_grid.addWidget(
                    self._preview_card(template_id), index // 2, index % 2
                )
        finally:
            self._busy(False)
        self.statusBar().showMessage(f"{len(selected)} önizleme hazır", 5000)

    def _preview_card(self, template_id: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #FFFEFA; border: 1px solid #D7D0C2; border-radius: 12px; }"
        )
        layout = QVBoxLayout(card)
        title = QLabel(self.engine.templates[template_id].display_name)
        title.setStyleSheet("font-weight: 700; color: #173C35; border: 0;")
        layout.addWidget(title)
        try:
            image = self.engine.render(
                template_id,
                self.current_product,
                self._current_copy(template_id),
                self.runtime.brand_kit(
                    shop_name=self.shop_input.text().strip() or "ECOVIA"
                ),
            )
            pixmap = QPixmap.fromImage(ImageQt.ImageQt(image))
            image.close()
            preview = QLabel()
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setPixmap(
                pixmap.scaled(
                    300,
                    450,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            preview.setAccessibleName(f"{title.text()} pin önizlemesi")
            layout.addWidget(preview)
        except (OSError, RenderError) as exc:
            error = QLabel(str(exc))
            error.setWordWrap(True)
            error.setStyleSheet("color: #9B3A2E; padding: 20px; border: 0;")
            layout.addWidget(error)
        return card

    def _export(self) -> None:
        if not self.current_product:
            return
        selected = self._selected_templates()
        if not selected:
            QMessageBox.information(
                self, "Şablon seçin", "En az bir şablon seçmelisiniz."
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Çıktı klasörünü seç",
            self.runtime.settings.export_directory,
        )
        if not folder:
            return
        copies = {
            template_id: self._current_copy(template_id) for template_id in selected
        }
        kwargs = {
            "brand": self.runtime.brand_kit(
                shop_name=self.shop_input.text().strip() or "ECOVIA"
            ),
            "copies": copies,
            "board_id": self.runtime.board_for_vertical(self.current_product.vertical)
            or None,
        }
        self._busy(True, "Pinler dışa aktarılıyor")
        try:
            try:
                result = self.exporter.export_product(
                    self.current_product, selected, folder, **kwargs
                )
            except FileExistsError:
                answer = QMessageBox.question(
                    self,
                    "Dosyalar zaten var",
                    "Aynı adlı çıktılar var. Üzerlerine yazılsın mı?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                result = self.exporter.export_product(
                    self.current_product,
                    selected,
                    folder,
                    overwrite=True,
                    **kwargs,
                )
        except (RenderError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Dışa aktarma başarısız", str(exc))
            return
        finally:
            self._busy(False)
        self.repository.save_drafts(result.drafts)
        self.statusBar().showMessage(f"{len(result.drafts)} pin dışa aktarıldı", 8000)
        QMessageBox.information(
            self,
            "Dışa aktarma tamamlandı",
            f"{len(result.drafts)} görsel ve schedule.csv kaydedildi:\n{result.output_dir}",
        )

    def _schedule_ready(self) -> None:
        drafts = self.repository.list_drafts(PinStatus.READY)
        if not drafts:
            self.statusBar().showMessage("Zamanlanacak hazır taslak yok", 5000)
            return
        products = {product.id: product for product in self.repository.list_products()}
        if any(
            not self.runtime.board_for_vertical(products[draft.product_id].vertical)
            for draft in drafts
            if draft.product_id in products
        ):
            QMessageBox.information(
                self, "Board seçin", "Ayarlar ekranından varsayılan board seçin."
            )
            return
        try:
            slots = SchedulePlanner.next_slots(
                utc_now(),
                len(drafts),
                self.runtime.settings.schedule_slots,
                max_daily=self.runtime.settings.max_daily_pins,
                time_zone=self.runtime.settings.time_zone,
            )
            scheduled: list[tuple[str, datetime, str]] = []
            validated_boards: set[str] = set()
            for draft, scheduled_at in zip(drafts, slots, strict=True):
                product = products.get(draft.product_id)
                board_id = self.runtime.board_for_vertical(
                    product.vertical if product else ""
                )
                if not board_id:
                    raise ValueError(
                        f"{draft.product_id} için Pinterest board seçilmedi"
                    )
                if board_id not in validated_boards:
                    self.runtime.validate_board_id(board_id)
                    validated_boards.add(board_id)
                if not draft.image_path or not draft.image_path.is_file():
                    raise ValueError(f"{draft.id} için pin görseli bulunamadı")
                scheduled.append((draft.id, scheduled_at, board_id))
            self.repository.schedule_many(scheduled)
        except (ApiError, KeyError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Zamanlama başarısız", str(exc))
            return
        self._refresh_queue()
        self.statusBar().showMessage(f"{len(drafts)} pin zamanlandı", 8000)

    def _publish_selected(self) -> None:
        row = self.queue_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Taslak seçin", "Yayınlamak için bir taslak seçin."
            )
            return
        draft_id = self.queue_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        draft = next(
            item for item in self.repository.list_drafts() if item.id == draft_id
        )
        product = next(
            (
                item
                for item in self.repository.list_products()
                if item.id == draft.product_id
            ),
            None,
        )
        board_id = self.runtime.board_for_vertical(product.vertical if product else "")
        if not board_id:
            QMessageBox.information(
                self, "Board seçin", "Ayarlar ekranından varsayılan board seçin."
            )
            return
        try:
            self.runtime.validate_board_id(board_id)
            self.repository.schedule(draft_id, utc_now(), board_id)
        except (ApiError, KeyError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Taslak yayınlanamadı", str(exc))
            return
        self._drain_queue(silent=False)

    def _drain_queue(self, *, silent: bool) -> None:
        try:
            connected = bool(self.runtime.get_secret(PINTEREST_TOKEN))
        except SecretStoreError as exc:
            if not silent:
                QMessageBox.critical(self, "Parola kasasına erişilemedi", str(exc))
            return
        if not connected:
            if not silent:
                QMessageBox.information(
                    self,
                    "Pinterest bağlı değil",
                    "Ayarlar ekranından Pinterest hesabını bağlayın.",
                )
            return
        self._busy(True, "Pinterest kuyruğu işleniyor")
        try:
            result = SchedulerService(
                self.repository,
                self.runtime.pinterest_client(),
                max_daily_pins=self.runtime.settings.max_daily_pins,
                max_attempts=self.runtime.settings.max_publish_attempts,
                min_interval_seconds=0,
                batch_limit=(
                    1 if silent else self.runtime.settings.headless_batch_size
                ),
                time_zone=self.runtime.settings.time_zone,
            ).drain_due()
        except (ApiError, OSError, SecretStoreError, ValueError) as exc:
            if not silent:
                QMessageBox.critical(self, "Pinterest yayını başarısız", str(exc))
            return
        finally:
            self._busy(False)
        self._refresh_queue()
        message = (
            f"{result.published} yayınlandı, {result.retried} yeniden denenecek, "
            f"{result.failed} başarısız"
            f", {result.unknown} sonucu belirsiz"
        )
        self.statusBar().showMessage(message, 10000)
        if not silent and result.daily_limit_reached:
            QMessageBox.information(
                self, "Günlük sınır", "Günlük Pinterest yayın sınırına ulaşıldı."
            )

    def _refresh_queue(self) -> None:
        drafts = self.repository.list_drafts()
        self.queue_table.setRowCount(len(drafts))
        for row, draft in enumerate(drafts):
            title = QTableWidgetItem(draft.title)
            title.setData(Qt.ItemDataRole.UserRole, draft.id)
            title.setToolTip(draft.last_error or draft.pinterest_pin_id or "")
            values = (
                title,
                QTableWidgetItem(draft.template_id),
                QTableWidgetItem(draft.status.value),
                QTableWidgetItem(
                    draft.scheduled_at.astimezone(
                        ZoneInfo(self.runtime.settings.time_zone)
                    ).strftime("%d.%m.%Y %H:%M")
                    if draft.scheduled_at
                    else "-"
                ),
                QTableWidgetItem(draft.board_id or "-"),
                QTableWidgetItem(str(draft.image_path or "")),
            )
            for column, item in enumerate(values):
                self.queue_table.setItem(row, column, item)

    def _update_counters(self) -> None:
        title_length = len(self.title_input.text())
        description_length = len(self.description_input.toPlainText())
        self.title_counter.setText(f"{title_length} / 100")
        self.description_counter.setText(f"{description_length} / 500")
        self.description_counter.setStyleSheet(
            "color: #9B3A2E;" if description_length > 500 else ""
        )

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_queue()
        elif index == 2:
            self.growth_page.refresh()

    def _settings_saved(self) -> None:
        self.shop_input.setText(self.runtime.settings.brand_shop_name)
        self.statusBar().showMessage("Ayarlar güncellendi", 5000)

    def _refresh_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        selected = 0
        for index, profile in enumerate(self.runtime.profiles()):
            self.profile_combo.addItem(profile.name, profile.id)
            if profile.id == self.runtime.profile_id:
                selected = index
        self.profile_combo.setCurrentIndex(selected)
        self.profile_combo.blockSignals(False)

    def _create_profile(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "Yeni profil", "Mağaza / hesap profilinin adı:"
        )
        if not accepted:
            return
        try:
            profile = self.runtime.create_profile(name)
            self.runtime.switch_profile(profile.id)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Profil oluşturulamadı", str(exc))
            return
        self._after_profile_switch()

    def _profile_changed(self, index: int) -> None:
        profile_id = self.profile_combo.itemData(index)
        if not profile_id or profile_id == self.runtime.profile_id:
            return
        try:
            self.runtime.switch_profile(str(profile_id))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Profil değiştirilemedi", str(exc))
            self._refresh_profiles()
            return
        self._after_profile_switch()

    def _after_profile_switch(self) -> None:
        self.repository = self.runtime.repository
        self.settings_page.reload()
        self.growth_page.refresh()
        self.shop_input.setText(self.runtime.settings.brand_shop_name)
        self._load_persisted_products()
        self._refresh_queue()
        self._refresh_profiles()
        self.statusBar().showMessage(
            f"Aktif profil: {self.runtime.profile_store.get(self.runtime.profile_id).name}",
            5000,
        )

    def _busy(self, active: bool, message: str = "") -> None:
        if active:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            if message:
                self.statusBar().showMessage(message)
        else:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("muted")
        return label

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        self.runtime.close()
        event.accept()
