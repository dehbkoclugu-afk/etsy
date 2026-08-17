from __future__ import annotations

import webbrowser

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pinforge.integrations.http import ApiError
from pinforge.integrations.oauth import parse_callback, save_token
from pinforge.runtime import (
    ANTHROPIC_API_KEY,
    ETSY_SHARED_SECRET,
    ETSY_TOKEN,
    PINTEREST_APP_SECRET,
    PINTEREST_TOKEN,
    PinForgeRuntime,
)
from pinforge.security import SecretStoreError
from pinforge.settings import AppSettings


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, runtime: PinForgeRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.setLayout(self._build())
        self._load()

    def _build(self) -> QVBoxLayout:
        root = QVBoxLayout()
        root.setContentsMargins(0, 18, 0, 0)
        root.setSpacing(14)
        intro = QLabel(
            "API anahtarları işletim sisteminin parola kasasında saklanır. "
            "Ayar dosyasına düz metin olarak yazılmaz."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        root.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(self._anthropic_card())
        row.addWidget(self._brand_card())
        row.addWidget(self._etsy_card())
        row.addWidget(self._pinterest_card())
        root.addLayout(row, 1)
        footer = QHBoxLayout()
        self.status = QLabel("Ayarlar yükleniyor")
        self.status.setObjectName("muted")
        footer.addWidget(self.status)
        footer.addStretch()
        save_button = QPushButton("Ayarları kaydet")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self.save)
        footer.addWidget(save_button)
        root.addLayout(footer)
        return root

    def _card(self, title: str) -> tuple[QFrame, QFormLayout]:
        card = QFrame()
        card.setObjectName("editorPanel")
        layout = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        layout.addStretch()
        return card, form

    def _anthropic_card(self) -> QFrame:
        card, form = self._card("AI metin üretimi")
        self.anthropic_key = self._password_field("Anthropic API anahtarı")
        self.anthropic_model = QLineEdit()
        form.addRow("API anahtarı", self.anthropic_key)
        form.addRow("Model", self.anthropic_model)
        return card

    def _brand_card(self) -> QFrame:
        card, form = self._card("Marka")
        self.brand_shop = QLineEdit()
        self.brand_primary = QLineEdit()
        self.brand_accent = QLineEdit()
        self.brand_surface = QLineEdit()
        self.brand_ink = QLineEdit()
        form.addRow("Mağaza", self.brand_shop)
        form.addRow("Ana renk", self.brand_primary)
        form.addRow("Vurgu", self.brand_accent)
        form.addRow("Zemin", self.brand_surface)
        form.addRow("Metin", self.brand_ink)
        return card

    def _etsy_card(self) -> QFrame:
        card, form = self._card("Etsy")
        self.etsy_keystring = QLineEdit()
        self.etsy_secret = self._password_field("Etsy shared secret")
        self.etsy_shop_id = QLineEdit()
        self.etsy_redirect = QLineEdit()
        form.addRow("Keystring", self.etsy_keystring)
        form.addRow("Shared secret", self.etsy_secret)
        form.addRow("Shop ID (opsiyonel)", self.etsy_shop_id)
        form.addRow("Redirect URI", self.etsy_redirect)
        connect = QPushButton("Etsy hesabını bağla")
        connect.clicked.connect(self._connect_etsy)
        form.addRow(connect)
        return card

    def _pinterest_card(self) -> QFrame:
        card, form = self._card("Pinterest")
        self.pinterest_app_id = QLineEdit()
        self.pinterest_secret = self._password_field("Pinterest app secret")
        self.pinterest_redirect = QLineEdit()
        self.pinterest_board = QComboBox()
        self.pinterest_board.setEditable(True)
        self.max_daily = QSpinBox()
        self.max_daily.setRange(1, 50)
        self.schedule_slots = QLineEdit()
        self.board_mappings = QLineEdit()
        self.time_zone = QLineEdit()
        self.allowed_hosts = QLineEdit()
        self.max_attempts = QSpinBox()
        self.max_attempts.setRange(1, 20)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 20)
        self.board_mappings.setPlaceholderText("airbnb=123, botanical=456")
        form.addRow("App ID", self.pinterest_app_id)
        form.addRow("App secret", self.pinterest_secret)
        form.addRow("Redirect URI", self.pinterest_redirect)
        form.addRow("Varsayılan board", self.pinterest_board)
        form.addRow("Dikey eşlemeleri", self.board_mappings)
        form.addRow("Günlük üst sınır", self.max_daily)
        form.addRow("Slotlar", self.schedule_slots)
        form.addRow("Zaman dilimi", self.time_zone)
        form.addRow("İzinli link hostları", self.allowed_hosts)
        form.addRow("En fazla deneme", self.max_attempts)
        form.addRow("Headless batch", self.batch_size)
        buttons = QHBoxLayout()
        connect = QPushButton("Hesabı bağla")
        connect.clicked.connect(self._connect_pinterest)
        boards = QPushButton("Boardları getir")
        boards.clicked.connect(self._load_boards)
        buttons.addWidget(connect)
        buttons.addWidget(boards)
        form.addRow(buttons)
        return card

    @staticmethod
    def _password_field(accessible_name: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Kaydetmek için girin")
        field.setAccessibleName(accessible_name)
        return field

    def _load(self) -> None:
        settings = self.runtime.settings
        self.anthropic_key.clear()
        self.etsy_secret.clear()
        self.pinterest_secret.clear()
        self.brand_shop.setText(settings.brand_shop_name)
        self.brand_primary.setText(settings.brand_primary)
        self.brand_accent.setText(settings.brand_accent)
        self.brand_surface.setText(settings.brand_surface)
        self.brand_ink.setText(settings.brand_ink)
        self.anthropic_model.setText(settings.anthropic_model)
        self.etsy_keystring.setText(settings.etsy_keystring)
        self.etsy_shop_id.setText(settings.etsy_shop_id)
        self.etsy_redirect.setText(settings.etsy_redirect_uri)
        self.pinterest_app_id.setText(settings.pinterest_app_id)
        self.pinterest_redirect.setText(settings.pinterest_redirect_uri)
        self.pinterest_board.clear()
        self.pinterest_board.setEditText(settings.pinterest_default_board_id)
        self.board_mappings.setText(
            ", ".join(
                f"{vertical}={board_id}"
                for vertical, board_id in sorted(
                    settings.pinterest_board_mappings.items()
                )
            )
        )
        self.max_daily.setValue(settings.max_daily_pins)
        self.schedule_slots.setText(", ".join(settings.schedule_slots))
        self.time_zone.setText(settings.time_zone)
        self.allowed_hosts.setText(", ".join(settings.allowed_destination_hosts))
        self.max_attempts.setValue(settings.max_publish_attempts)
        self.batch_size.setValue(settings.headless_batch_size)
        self.status.setText("Ayarlar hazır")

    def reload(self) -> None:
        self._load()

    def save(self) -> bool:
        try:
            slots = tuple(
                value.strip()
                for value in self.schedule_slots.text().split(",")
                if value.strip()
            )
            mappings = _parse_mappings(self.board_mappings.text())
            colors = (
                self.brand_primary.text().strip(),
                self.brand_accent.text().strip(),
                self.brand_surface.text().strip(),
                self.brand_ink.text().strip(),
            )
            if not all(_valid_hex_color(value) for value in colors):
                raise ValueError("Marka renkleri #RRGGBB biçiminde olmalı")
            settings = AppSettings(
                brand_shop_name=self.brand_shop.text().strip() or "ECOVIA",
                brand_primary=colors[0],
                brand_accent=colors[1],
                brand_surface=colors[2],
                brand_ink=colors[3],
                anthropic_model=self.anthropic_model.text().strip()
                or "claude-haiku-4-5-20251001",
                etsy_keystring=self.etsy_keystring.text().strip(),
                etsy_shop_id=self.etsy_shop_id.text().strip(),
                etsy_redirect_uri=self.etsy_redirect.text().strip(),
                pinterest_app_id=self.pinterest_app_id.text().strip(),
                pinterest_redirect_uri=self.pinterest_redirect.text().strip(),
                pinterest_default_board_id=str(
                    self.pinterest_board.currentData()
                    or self.pinterest_board.currentText()
                ).strip(),
                pinterest_board_mappings=mappings,
                export_directory=self.runtime.settings.export_directory,
                import_cache_directory=self.runtime.settings.import_cache_directory,
                schedule_slots=slots,
                max_daily_pins=self.max_daily.value(),
                max_publish_attempts=self.max_attempts.value(),
                headless_batch_size=self.batch_size.value(),
                time_zone=self.time_zone.text().strip() or "Europe/Istanbul",
                ai_language=self.runtime.settings.ai_language,
                allowed_destination_hosts=tuple(
                    value.strip()
                    for value in self.allowed_hosts.text().split(",")
                    if value.strip()
                ),
                cache_max_megabytes=self.runtime.settings.cache_max_megabytes,
                cache_max_age_days=self.runtime.settings.cache_max_age_days,
            )
            self.runtime.save_settings(settings)
            self._save_secret(ANTHROPIC_API_KEY, self.anthropic_key)
            self._save_secret(ETSY_SHARED_SECRET, self.etsy_secret)
            self._save_secret(PINTEREST_APP_SECRET, self.pinterest_secret)
        except (OSError, SecretStoreError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Ayarlar kaydedilemedi", str(exc))
            return False
        self.status.setText("Ayarlar kaydedildi")
        self.saved.emit()
        return True

    def _save_secret(self, name: str, field: QLineEdit) -> None:
        value = field.text().strip()
        if value:
            self.runtime.set_secret(name, value)
            field.clear()

    def _connect_etsy(self) -> None:
        if not self.save():
            return
        try:
            oauth = self.runtime.etsy_oauth()
            attempt = oauth.begin(self.runtime.settings.etsy_redirect_uri)
            webbrowser.open(attempt.authorization_url)
            callback, accepted = QInputDialog.getMultiLineText(
                self,
                "Etsy bağlantısı",
                "Etsy yönlendirmesinden sonra tarayıcıdaki tam callback URL'yi buraya yapıştırın:",
            )
            if not accepted:
                return
            token = oauth.exchange(parse_callback(callback, attempt.state), attempt)
            save_token(self.runtime.secrets, self.runtime.secret_key(ETSY_TOKEN), token)
            self.runtime.repository.audit(
                "provider_connected",
                entity_type="auth",
                provider="etsy",
                details={"scopes": token.scopes, "account_id": token.account_id},
            )
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Etsy bağlantısı başarısız", str(exc))
            return
        self.status.setText("Etsy hesabı bağlı")

    def _connect_pinterest(self) -> None:
        if not self.save():
            return
        try:
            oauth = self.runtime.pinterest_oauth()
            attempt = oauth.begin(self.runtime.settings.pinterest_redirect_uri)
            webbrowser.open(attempt.authorization_url)
            callback, accepted = QInputDialog.getMultiLineText(
                self,
                "Pinterest bağlantısı",
                "Pinterest yönlendirmesinden sonra tarayıcıdaki tam callback URL'yi buraya yapıştırın:",
            )
            if not accepted:
                return
            token = oauth.exchange(parse_callback(callback, attempt.state), attempt)
            save_token(
                self.runtime.secrets,
                self.runtime.secret_key(PINTEREST_TOKEN),
                token,
            )
            self.runtime.repository.audit(
                "provider_connected",
                entity_type="auth",
                provider="pinterest",
                details={"scopes": token.scopes, "account_id": token.account_id},
            )
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Pinterest bağlantısı başarısız", str(exc))
            return
        self.status.setText("Pinterest hesabı bağlı")
        self._load_boards()

    def _load_boards(self) -> None:
        try:
            boards = self.runtime.pinterest_client().list_boards()
        except (ApiError, SecretStoreError, ValueError) as exc:
            QMessageBox.critical(self, "Boardlar alınamadı", str(exc))
            return
        selected = self.runtime.settings.pinterest_default_board_id
        self.pinterest_board.clear()
        for board in boards:
            self.pinterest_board.addItem(board.name, board.id)
            if board.id == selected:
                self.pinterest_board.setCurrentIndex(self.pinterest_board.count() - 1)
        self.status.setText(f"{len(boards)} Pinterest board bulundu")


def _valid_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])


def _parse_mappings(raw: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in raw.split(","):
        if not value.strip():
            continue
        try:
            vertical, board_id = value.split("=", 1)
        except ValueError as exc:
            raise ValueError(
                "Board eşlemeleri dikey=board_id biçiminde olmalı"
            ) from exc
        vertical, board_id = vertical.strip(), board_id.strip()
        if not vertical or not board_id:
            raise ValueError("Board eşlemesinde dikey ve board ID boş olamaz")
        mappings[vertical] = board_id
    return mappings
