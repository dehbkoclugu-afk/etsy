from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pinforge.application.exporter import BundleExporter
from pinforge.clock import utc_now
from pinforge.domain.models import BrandKit, PinDraft, PinStatus, SourceProduct
from pinforge.growth.service import GrowthService
from pinforge.importers.folder import FolderImporter, ManifestError
from pinforge.integrations.http import ApiError
from pinforge.integrations.browser_automation import (
    PersistentBrowser,
    PinterestBrowserClient,
)
from pinforge.integrations.oauth import OAuthAttempt, parse_callback, save_token
from pinforge.observability import configure_logging
from pinforge.rendering.engine import RenderEngine, RenderError
from pinforge.runtime import (
    ETSY_TOKEN,
    PINTEREST_TOKEN,
    PinForgeRuntime,
    default_data_directory,
)
from pinforge.scheduling import SchedulePlanner, SchedulerService
from pinforge.security import SecretStore, SecretStoreError

ETSY_ATTEMPT = "etsy_oauth_attempt"
PINTEREST_ATTEMPT = "pinterest_oauth_attempt"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="pinforge", description="Pinterest pin üretim aracı"
    )
    root.add_argument("--data-dir", type=Path, default=default_data_directory())
    root.add_argument("--verbose", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="products.json dosyasını doğrula")
    validate.add_argument("folder", type=Path)

    render = sub.add_parser("render-folder", help="Bir klasördeki ürünleri dışa aktar")
    render.add_argument("folder", type=Path)
    render.add_argument("output", type=Path)
    render.add_argument(
        "--templates",
        default=",".join(RenderEngine().templates),
        help="Virgülle ayrılmış şablon kimlikleri",
    )
    render.add_argument("--shop", default="ECOVIA")
    render.add_argument("--overwrite", action="store_true")

    copy = sub.add_parser("generate-copy", help="Bir ürün için AI pin metinleri üret")
    copy.add_argument("folder", type=Path)
    copy.add_argument("product_id")
    copy.add_argument("--templates", default=",".join(RenderEngine().templates))
    copy.add_argument("--keywords", default="")

    etsy = sub.add_parser("etsy-import", help="Etsy mağazasını yerel klasöre aktar")
    etsy.add_argument("output", type=Path)

    sub.add_parser("pinterest-boards", help="Pinterest board listesini göster")

    browser_login = sub.add_parser(
        "browser-login",
        help="Pinterest için kullanılacak kalıcı tarayıcı oturumunu aç",
    )
    browser_login.add_argument(
        "--channel", choices=("chrome", "msedge"), default="chrome"
    )

    auto_run = sub.add_parser(
        "auto-run",
        help="Etsy API'den ürün çek, Pin üret ve Pinterest'e otomatik yayınla",
    )
    auto_run.add_argument("--board", required=True, help="Pinterest pano adı")
    auto_run.add_argument("--limit", type=int, default=1)
    auto_run.add_argument("--template", default="text_overlay")
    auto_run.add_argument("--channel", choices=("chrome", "msedge"), default="chrome")
    auto_run.add_argument(
        "--headless",
        action="store_true",
        help="Kaydedilmiş oturumla tarayıcıyı görünmeden çalıştır",
    )
    auto_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Ürünleri çekip Pin üret; Pinterest'e yükleme",
    )

    auth_start = sub.add_parser("auth-start", help="OAuth bağlantısını başlat")
    auth_start.add_argument("provider", choices=("etsy", "pinterest"))
    auth_start.add_argument("--no-browser", action="store_true")

    auth_complete = sub.add_parser(
        "auth-complete", help="OAuth callback ile bağlantıyı tamamla"
    )
    auth_complete.add_argument("provider", choices=("etsy", "pinterest"))
    auth_complete.add_argument(
        "callback",
        nargs="?",
        default="-",
        help="Callback URL; verilmezse standart girdiden güvenli biçimde okunur",
    )

    schedule = sub.add_parser(
        "schedule-ready", help="Hazır taslakları zamanlama slotlarına dağıt"
    )
    schedule.add_argument("--board-id", default="")
    schedule.add_argument("--start", default="")

    sub.add_parser("drain-queue", help="Zamanı gelen Pinterest taslaklarını yayınla")
    logout = sub.add_parser("logout", help="Sağlayıcı bağlantısını yerelden kaldır")
    logout.add_argument("provider", choices=("etsy", "pinterest"))
    reconcile = sub.add_parser(
        "reconcile-unknown", help="Belirsiz yayını uzaktaki pin kimliğiyle tamamla"
    )
    reconcile.add_argument("draft_id")
    reconcile.add_argument("remote_id")
    reconcile.add_argument("--remote-url", default="")
    requeue = sub.add_parser(
        "requeue-unknown", help="Belirsiz yayını açık onayla yeniden kuyruğa al"
    )
    requeue.add_argument("draft_id")
    sub.add_parser("health", help="Yerel backend bütünlüğünü kontrol et")
    metrics_sync = sub.add_parser(
        "metrics-sync", help="Yayınlanan pinlerin Pinterest metriklerini al"
    )
    metrics_sync.add_argument("--days", type=int, default=30)
    metrics_import = sub.add_parser(
        "metrics-import", help="Pinterest metriklerini CSV'den içe aktar"
    )
    metrics_import.add_argument("csv", type=Path)
    insights = sub.add_parser("insights", help="Performans öğrenme raporunu göster")
    insights.add_argument(
        "--dimension", choices=("template", "hour", "weekday"), default="template"
    )
    trends = sub.add_parser("trends-import", help="Trend terimlerini CSV'den al")
    trends.add_argument("csv", type=Path)
    seo = sub.add_parser("seo-suggest", help="Ürün için SEO kelimeleri öner")
    seo.add_argument("product_id")
    experiment = sub.add_parser("experiment-create", help="A/B testi oluştur")
    experiment.add_argument("product_id")
    experiment.add_argument("output", type=Path)
    experiment.add_argument("--templates", default="mockup_hero,text_overlay")
    winner = sub.add_parser("experiment-winner", help="A/B testi kazananını hesapla")
    winner.add_argument("experiment_id")
    winner.add_argument("--finalize", action="store_true")
    fill = sub.add_parser("calendar-fill", help="Hazır pinlerle takvimi doldur")
    fill.add_argument("--board-id", default="")
    fill.add_argument("--start", default="")
    sub.add_parser("profiles", help="Hesap profillerini listele")
    profile_create = sub.add_parser("profile-create", help="Yeni hesap profili oluştur")
    profile_create.add_argument("name")
    profile_switch = sub.add_parser("profile-switch", help="Aktif hesabı değiştir")
    profile_switch.add_argument("profile_id")
    sub.add_parser("gui", help="Masaüstü arayüzünü aç")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    if args.command == "gui":
        from pinforge.ui.app import main as gui_main

        return gui_main(data_directory=args.data_dir)
    if args.command == "browser-login":
        return _browser_login(args)
    if args.command in {"validate", "render-folder"}:
        return _local_command(args)

    runtime = PinForgeRuntime(args.data_dir, SecretStore())
    try:
        if args.command == "generate-copy":
            return _generate_copy(args, runtime)
        if args.command == "etsy-import":
            return _etsy_import(args, runtime)
        if args.command == "auto-run":
            return _automation_run(args, runtime)
        if args.command == "pinterest-boards":
            for board in runtime.pinterest_client().list_boards():
                print(f"{board.id}\t{board.name}")
            return 0
        if args.command == "auth-start":
            return _auth_start(args, runtime)
        if args.command == "auth-complete":
            return _auth_complete(args, runtime)
        if args.command == "schedule-ready":
            return _schedule_ready(args, runtime)
        if args.command == "calendar-fill":
            return _schedule_ready(args, runtime)
        if args.command == "drain-queue":
            scheduler = SchedulerService(
                runtime.repository,
                runtime.pinterest_client(),
                max_daily_pins=runtime.settings.max_daily_pins,
                max_attempts=runtime.settings.max_publish_attempts,
                batch_limit=runtime.settings.headless_batch_size,
                time_zone=runtime.settings.time_zone,
            )
            result = scheduler.drain_due()
            print(
                f"published={result.published} retried={result.retried} "
                f"failed={result.failed} daily_limit={result.daily_limit_reached}"
                f" unknown={result.unknown} recovered={result.recovered_receipts}"
            )
            return 3 if result.failed or result.unknown else 0
        if args.command == "logout":
            runtime.disconnect(args.provider)
            print(f"{args.provider} bağlantısı kaldırıldı")
            return 0
        if args.command == "reconcile-unknown":
            runtime.repository.reconcile_unknown(
                args.draft_id, args.remote_id, remote_url=args.remote_url or None
            )
            print(f"{args.draft_id} uzlaştırıldı")
            return 0
        if args.command == "requeue-unknown":
            runtime.repository.requeue_unknown(args.draft_id, utc_now())
            print(f"{args.draft_id} açık onayla yeniden kuyruğa alındı")
            return 0
        if args.command == "health":
            check = runtime.repository.connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]
            print(
                f"database={check} products={len(runtime.repository.list_products())} drafts={len(runtime.repository.list_drafts())}"
            )
            return 0 if check == "ok" else 4
        growth = GrowthService(runtime.repository, time_zone=runtime.settings.time_zone)
        if args.command == "metrics-sync":
            count, issues = growth.sync_pinterest(
                runtime.pinterest_client(), days=args.days
            )
            print(f"{count} pin metriği güncellendi, {len(issues)} sorun")
            for issue in issues:
                print(f"- {issue}")
            return 0 if not issues else 3
        if args.command == "metrics-import":
            print(f"{growth.import_metrics_csv(args.csv)} metrik satırı içe aktarıldı")
            return 0
        if args.command == "insights":
            for insight in growth.insights(args.dimension):
                print(
                    f"{insight.value}\timpressions={insight.impressions} "
                    f"engagements={insight.engagements} score={insight.score:.4f}"
                )
            return 0
        if args.command == "trends-import":
            print(f"{growth.import_trends_csv(args.csv)} trend terimi içe aktarıldı")
            return 0
        if args.command == "seo-suggest":
            product = next(
                (
                    item
                    for item in runtime.repository.list_products()
                    if item.id == args.product_id
                ),
                None,
            )
            if product is None:
                raise ValueError(f"Ürün bulunamadı: {args.product_id}")
            for suggestion in growth.seo_suggestions(product):
                print(f"{suggestion.term}\t{suggestion.score:.2f}\t{suggestion.reason}")
            return 0
        if args.command == "experiment-create":
            product = next(
                (
                    item
                    for item in runtime.repository.list_products()
                    if item.id == args.product_id
                ),
                None,
            )
            if product is None:
                raise ValueError(f"Ürün bulunamadı: {args.product_id}")
            templates = _values(args.templates)
            exported = BundleExporter().export_product(product, templates, args.output)
            runtime.repository.save_drafts(exported.drafts)
            experiment_id = growth.create_experiment(
                product.id, f"{product.title} A/B", exported.drafts
            )
            print(f"experiment={experiment_id} variants={len(exported.drafts)}")
            return 0
        if args.command == "experiment-winner":
            experiment_result = growth.experiment_result(
                args.experiment_id, finalize=args.finalize
            )
            print(
                f"winner={experiment_result.winner_label or '-'} "
                f"status={experiment_result.status}"
            )
            for variant in experiment_result.variants:
                print(
                    f"{variant.value}\timpressions={variant.impressions} "
                    f"score={variant.score:.4f}"
                )
            return 0 if experiment_result.winner_label else 3
        if args.command == "profiles":
            for profile in runtime.profiles():
                marker = "*" if profile.id == runtime.profile_id else " "
                print(f"{marker} {profile.id}\t{profile.name}")
            return 0
        if args.command == "profile-create":
            profile = runtime.create_profile(args.name)
            print(f"{profile.id}\t{profile.name}")
            return 0
        if args.command == "profile-switch":
            runtime.switch_profile(args.profile_id)
            print(f"Aktif profil: {runtime.profile_id}")
            return 0
        return 2
    except (
        ApiError,
        FileExistsError,
        ManifestError,
        OSError,
        RenderError,
        SecretStoreError,
        ValueError,
    ) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2
    finally:
        runtime.close()


def _local_command(args: argparse.Namespace) -> int:
    try:
        result = FolderImporter.load(args.folder)
        if args.command == "validate":
            print(f"{len(result.products)} ürün geçerli, {len(result.issues)} sorun")
            for issue in result.issues:
                print(f"- Kayıt {issue.index + 1}: {issue.message}")
            return 0
        template_ids = _values(args.templates)
        exporter = BundleExporter()
        for product in result.products:
            destination = args.output / product.id
            exported = exporter.export_product(
                product,
                template_ids,
                destination,
                brand=BrandKit(shop_name=args.shop),
                overwrite=args.overwrite,
            )
            print(
                f"{product.title}: {len(exported.drafts)} pin -> {exported.output_dir}"
            )
        return 0
    except (ManifestError, RenderError, FileExistsError, ValueError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2


def _browser_login(args: argparse.Namespace) -> int:
    profile = args.data_dir.expanduser().resolve() / "browser-profile"
    try:
        with PersistentBrowser(profile, channel=args.channel) as browser:
            browser.prepare_pinterest_login()
            print(
                "Pinterest hesabına açılan pencerede giriş yapın. "
                "Bitince bu terminalde Enter'a basın."
            )
            input()
        print("Kalıcı Pinterest tarayıcı oturumu kaydedildi")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2


def _automation_run(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    if not 1 <= args.limit <= 20:
        print("Hata: --limit 1-20 arasında olmalı", file=sys.stderr)
        return 2
    try:
        output = runtime.data_directory / "automation"
        cache = output / "etsy-cache"
        products = runtime.etsy_client().import_shop(
            runtime.settings.etsy_shop_id,
            cache,
            limit=args.limit,
        )
        runtime.repository.save_products(products)
        existing = {
            (draft.product_id, draft.template_id): draft
            for draft in runtime.repository.list_drafts()
        }
        ready: list[PinDraft] = []
        for product in products:
            previous = existing.get((product.id, args.template))
            if previous is not None and previous.status is PinStatus.PUBLISHED:
                print(f"Atlandı (daha önce yayınlandı): {product.title}")
                continue
            if (
                previous is not None
                and previous.status is PinStatus.READY
                and previous.image_path is not None
                and previous.image_path.is_file()
            ):
                draft = previous
            else:
                exported = BundleExporter().export_product(
                    product,
                    (args.template,),
                    output / "pins" / product.id,
                    brand=runtime.brand_kit(),
                    overwrite=True,
                )
                runtime.repository.save_drafts(exported.drafts)
                draft = exported.drafts[0]
            ready.append(draft)
            print(f"Hazırlandı: {product.title}")
        if args.dry_run:
            print(f"dry-run: {len(ready)} Pin hazırlandı, yayınlanmadı")
            return 0
        for draft in ready:
            runtime.repository.schedule(draft.id, utc_now(), args.board)
        profile = args.data_dir.expanduser().resolve() / "browser-profile"
        with PersistentBrowser(
            profile,
            channel=args.channel,
            headless=args.headless,
        ) as browser:
            scheduler = SchedulerService(
                runtime.repository,
                PinterestBrowserClient(browser, board_name=args.board),
                max_daily_pins=runtime.settings.max_daily_pins,
                max_attempts=1,
                min_interval_seconds=3,
                batch_limit=args.limit,
                time_zone=runtime.settings.time_zone,
            )
            result = scheduler.drain_due()
            print(
                f"çekilen={len(products)} hazırlanan={len(ready)} "
                f"yayınlanan={result.published} belirsiz={result.unknown} "
                f"hatalı={result.failed}"
            )
            return 0 if not result.failed and not result.unknown else 3
    except (ApiError, OSError, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2


def _generate_copy(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    products = FolderImporter.load(args.folder).products
    product = next((item for item in products if item.id == args.product_id), None)
    if product is None:
        raise ValueError(f"Ürün bulunamadı: {args.product_id}")
    copies = runtime.copy_generator().generate(
        product,
        _values(args.templates),
        target_keywords=tuple(_values(args.keywords)),
    )
    print(
        json.dumps(
            {key: asdict(value) for key, value in copies.items()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _etsy_import(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    output = args.output.expanduser().resolve()
    products = runtime.etsy_client().import_shop(runtime.settings.etsy_shop_id, output)
    runtime.repository.save_products(products)
    runtime.repository.mark_missing_products_inactive(
        product.id for product in products
    )
    runtime.repository.audit(
        "etsy_imported",
        entity_type="import",
        provider="etsy",
        details={"count": len(products)},
    )
    _write_manifest(output, products)
    print(f"{len(products)} Etsy ürünü -> {output}")
    return 0


def _auth_start(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    if args.provider == "etsy":
        attempt = runtime.etsy_oauth().begin(runtime.settings.etsy_redirect_uri)
    else:
        attempt = runtime.pinterest_oauth().begin(
            runtime.settings.pinterest_redirect_uri
        )
    secret_name = ETSY_ATTEMPT if args.provider == "etsy" else PINTEREST_ATTEMPT
    runtime.set_secret(secret_name, json.dumps(asdict(attempt)))
    print(attempt.authorization_url)
    if not args.no_browser:
        webbrowser.open(attempt.authorization_url)
    return 0


def _auth_complete(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    secret_name = ETSY_ATTEMPT if args.provider == "etsy" else PINTEREST_ATTEMPT
    secret_name = runtime.secret_key(secret_name)
    raw_attempt = runtime.secrets.get(secret_name)
    if not raw_attempt:
        raise ValueError("Önce auth-start çalıştırılmalı")
    runtime.secrets.delete(secret_name)
    payload = json.loads(raw_attempt)
    attempt = OAuthAttempt(**payload)
    callback = sys.stdin.readline().strip() if args.callback == "-" else args.callback
    code = parse_callback(callback, attempt.state)
    if args.provider == "etsy":
        token = runtime.etsy_oauth().exchange(code, attempt)
        save_token(runtime.secrets, runtime.secret_key(ETSY_TOKEN), token)
    else:
        token = runtime.pinterest_oauth().exchange(code, attempt)
        save_token(runtime.secrets, runtime.secret_key(PINTEREST_TOKEN), token)
    runtime.repository.audit(
        "provider_connected",
        entity_type="auth",
        provider=args.provider,
        details={"scopes": token.scopes, "account_id": token.account_id},
    )
    print(f"{args.provider} bağlantısı tamamlandı")
    return 0


def _schedule_ready(args: argparse.Namespace, runtime: PinForgeRuntime) -> int:
    drafts = runtime.repository.list_drafts(PinStatus.READY)
    products = {product.id: product for product in runtime.repository.list_products()}
    start = datetime.fromisoformat(args.start) if args.start else utc_now()
    slots = SchedulePlanner.next_slots(
        start,
        len(drafts),
        runtime.settings.schedule_slots,
        max_daily=runtime.settings.max_daily_pins,
        time_zone=runtime.settings.time_zone,
    )
    scheduled: list[tuple[str, datetime, str]] = []
    validated_boards: set[str] = set()
    for draft, scheduled_at in zip(drafts, slots, strict=True):
        product = products.get(draft.product_id)
        board_id = args.board_id or runtime.board_for_vertical(
            product.vertical if product else ""
        )
        if not board_id:
            raise ValueError(f"{draft.product_id} için Pinterest board ID gerekli")
        if board_id not in validated_boards:
            runtime.validate_board_id(board_id)
            validated_boards.add(board_id)
        if not draft.image_path or not draft.image_path.is_file():
            raise ValueError(f"{draft.id} için yayın görseli bulunamadı")
        scheduled.append((draft.id, scheduled_at, board_id))
    runtime.repository.schedule_many(scheduled)
    print(f"{len(drafts)} taslak zamanlandı")
    return 0


def _values(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(",") if value.strip()]


def _write_manifest(folder: Path, products: tuple[SourceProduct, ...]) -> None:
    records = []
    for product in products:
        records.append(
            {
                "id": product.id,
                "title": product.title,
                "listing_url": product.listing_url,
                "price": product.price,
                "currency": product.currency,
                "vertical": product.vertical,
                "tags": list(product.tags),
                "description": product.description,
                "images": [
                    str(path.relative_to(folder)) for path in product.image_paths
                ],
            }
        )
    path = folder / "products.json"
    temporary = folder / ".products.json.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"products": records}, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
