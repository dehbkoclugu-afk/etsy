# PinForge

PinForge, Etsy ürünlerinden veya yerel görsellerden 1000 × 1500 Pinterest pinleri
üreten bir Python masaüstü uygulamasıdır. Beş tasarım şablonu, AI metin üretimi,
PNG/CSV dışa aktarma, SQLite yayın kuyruğu, Etsy içe aktarma ve Pinterest yayınlama
tek uygulamada bulunur. Yerel klasör ve CSV akışı API hesabı olmadan da çalışır.

## Özellikler

- Beş şablon: Mockup Hero, List Stack, Split Compare, Text Overlay ve Grid Preview
- Kalıcı marka paleti, mağaza adı ve canlı önizleme
- Etsy Open API v3 OAuth/PKCE ile aktif ürün ve görsel içe aktarma
- Anthropic Messages API structured output ile şablona özel pin metinleri
- Pinterest API v5 OAuth, board listesi ve statik pin yayınlama
- Dikey → board eşlemesi, günlük üst sınır ve belirli saat slotları
- Hata sonrası kontrollü exponential backoff, rate-limit desteği ve yarım kalan iş kurtarma
- Atomik günlük kota, worker lease'leri, dead-letter kuyruğu ve çift yayın koruması
- UTC tabanlı kayıtlar; IANA zaman dilimi ve yaz/kış saati güvenli zamanlama
- İzinli hedef domainleri, boyut/MIME/piksel sınırları ve güvenli HTTP yönlendirmeleri
- JSON audit kayıtları, sağlık kontrolü ve belirsiz yayın uzlaştırma komutları
- Uygulama kapalıyken çalışabilen CLI ve Windows Task Scheduler kurulumu
- API yoksa PNG görselleri ve `schedule.csv` üreten bağımsız dışa aktarma yolu

## Büyüme özellikleri

Arayüzdeki **Büyüme** sekmesi beş yeni çalışma alanını bir araya getirir:

- **Performans öğrenmesi:** Pinterest gösterim, kaydetme, pin tıklaması ve dış bağlantı
  tıklamalarından şablon, yerel saat ve haftanın günü sıralaması çıkarır.
- **A/B testleri:** Aynı ürünün iki yaratıcı varyantını mevcut güvenli render ve yayın
  kuyruğunda izler; yeterli metrik geldiğinde kazananı belirler.
- **İçerik takvimi:** Hazır pinleri slotlara otomatik dağıtır; kuyruğa alınmış pinler
  aylık takvimde günler arasında sürüklenebilir.
- **Çoklu hesap profilleri:** Her mağazayı ayrı ayar, SQLite veritabanı, önbellek ve
  parola-kasası ad alanında tutar. Üst çubuktan profil oluşturup geçiş yapılabilir.
- **Trend ve SEO asistanı:** Ürün başlığı/etiketleri, içe aktarılan trendler ve geçmiş
  performansı açıklanabilir bir puanda birleştirir.

Pinterest Analytics erişimi bulunmayan uygulamalar metrikleri CSV ile alabilir:

```csv
pin_id,date,impressions,saves,pin_clicks,outbound_clicks
123456,2026-08-02,1200,30,10,8
```

Trend CSV biçimi:

```csv
term,score,vertical,date
welcome book,92,airbnb,2026-08-02
```

Daha önce bağlanmış Pinterest profillerinde Analytics senkronu için hesabı bir kez
yeniden bağlayarak yeni `user_accounts:read` iznini onaylamak gerekebilir. Yayınlama
bu izinden bağımsız çalışmaya devam eder.

## Kurulum

Python 3.11 veya daha yenisi gerekir.

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
pinforge-gui
```

Linux/macOS aktivasyonu: `source .venv/bin/activate`.

## Yerel ürünlerle kullanım

Arayüzde **Ürün klasörü aç** ile `products.json` içeren klasörü seçin. Örnek kayıt:

```json
{
  "products": [
    {
      "id": "welcome-book",
      "title": "Modern Airbnb Welcome Book Template",
      "listing_url": "https://www.etsy.com/listing/123/example",
      "price": 12.9,
      "currency": "USD",
      "vertical": "airbnb",
      "tags": ["airbnb", "welcome book"],
      "images": ["product.jpg"]
    }
  ]
}
```

CLI ile doğrulama ve toplu render:

```bash
pinforge validate examples
pinforge render-folder examples exports
```

## API kurulumu

Arayüzde **Ayarlar** sekmesini açın. Anahtarlar `settings.json` içine yazılmaz;
işletim sisteminin parola kasasında saklanır.

### Anthropic

Anthropic API anahtarını girin. Varsayılan model sabit sürümlü
`claude-haiku-4-5-20251001` değeridir. **AI ile 5 farklı metin üret** tek API
isteğinde seçili şablonların tamamını üretir.

### Etsy

Etsy Developer uygulamasındaki keystring, shared secret, sayısal Shop ID ve kayıtlı
HTTPS redirect URI değerlerini girin. Sonra **Etsy hesabını bağla** düğmesine basın;
tarayıcıdaki yetkilendirme tamamlanınca tam callback URL'yi uygulamaya yapıştırın.
Uygulama yalnızca `listings_r` ve `shops_r` izinlerini ister.

### Pinterest

Pinterest uygulamasındaki App ID, App secret ve kayıtlı redirect URI değerlerini
girin. **Hesabı bağla** ile OAuth'u tamamlayın, ardından **Boardları getir** ile
varsayılan boardu seçin. İsterseniz `airbnb=123, botanical=456` biçiminde dikey
eşlemeleri ekleyin. Pinterest'e gönderilebilecek hedef domainleri virgülle ayrılmış
olarak tanımlayın; varsayılan değer `etsy.com` alan adıdır.

## Kuyruk ve arka plan yayını

PNG/CSV dışa aktarıldığında taslaklar SQLite'a kaydedilir. **Hazırları zamanla**
seçili saat slotlarına dağıtır. **Zamanı gelenleri yayınla** kuyruğu işler. HTTP 429
ve geçici sunucu/ağ hataları otomatik olarak ileriki bir saate alınır; doğrulama ve
kalıcı API hataları yeniden denenmeden hata kuyruğuna alınır. Maksimum denemeye
ulaşan işler `dead_letter` olur. Bağlantı, zaman aşımı veya yerel kayıt sorunu
nedeniyle uzak sonucun kesin olmadığı yayınlar otomatik tekrar gönderilmez;
`publish_unknown` durumunda insan uzlaştırması bekler.

Komut satırında tek seferlik kuyruk çalıştırma:

```bash
pinforge drain-queue
pinforge health
pinforge reconcile-unknown DRAFT_ID PINTEREST_PIN_ID --remote-url URL
pinforge requeue-unknown DRAFT_ID
pinforge logout pinterest
```

`requeue-unknown` yalnız Pinterest'te pin bulunmadığı doğrulandıktan sonra
kullanılmalıdır; aksi halde çift pin oluşturabilir. Uzak pin mevcutsa
`reconcile-unknown` ile yerel kayıt tamamlanır.

Windows'ta bunu beş dakikada bir arka planda çalıştıran görevi kurmak için:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_scheduler.ps1 `
  -PythonPath "$PWD\.venv\Scripts\python.exe" `
  -ProjectPath "$PWD"
```

Görev mevcut kullanıcıyla ve parola istemeden çalışır. Bilgisayar çevrimdışıysa
istek bir sonraki güvenli denemeye bırakılır.

## CLI başvurusu

```bash
pinforge --help
pinforge generate-copy PRODUCT_FOLDER PRODUCT_ID
pinforge etsy-import OUTPUT_FOLDER
pinforge pinterest-boards
pinforge auth-start etsy
pinforge auth-complete etsy "CALLBACK_URL"
pinforge schedule-ready --board-id BOARD_ID
pinforge drain-queue
pinforge health
pinforge logout etsy
pinforge metrics-sync --days 30
pinforge metrics-import metrics.csv
pinforge insights --dimension template
pinforge trends-import trends.csv
pinforge seo-suggest PRODUCT_ID
pinforge experiment-create PRODUCT_ID OUTPUT_FOLDER
pinforge experiment-winner EXPERIMENT_ID --finalize
pinforge calendar-fill
pinforge profiles
pinforge profile-create "İkinci Mağaza"
pinforge profile-switch PROFILE_ID
```

Headless kurulumlarda parola kasası yerine `PINFORGE_ANTHROPIC_API_KEY`,
`PINFORGE_ETSY_SHARED_SECRET` ve `PINFORGE_PINTEREST_APP_SECRET` ortam
değişkenleri kullanılabilir. OAuth tokenları ilk bağlantıdan sonra parola kasasında
kalır.

## Windows EXE

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

`PinForge-Windows.zip` çalıştırılabilir uygulamayı içerir. GitHub'daki
**Build Windows app** iş akışı da bir sürüm etiketi gönderildiğinde veya elle
başlatıldığında testleri çalıştırıp aynı ZIP artifact'ini üretir.

## Geliştirme

```bash
python -m pip install -r requirements.lock -e .
pytest --cov=pinforge
ruff check src tests
mypy src/pinforge
pip-audit -r requirements.lock
```

Testler gerçek API çağrısı yapmadan OAuth, API sayfalama, structured output,
görsel önbelleği, pin payload'ı, eşzamanlı SQLite claim/kota işlemi, makbuz
kurtarma, DST zamanlaması ve retry/dead-letter davranışlarını sınar. Üretim ve CI
kurulumlarında denetlenmiş tam sürümler için `requirements.lock` kullanılabilir.

## SaaS geliştirme

Django web uygulaması masaüstü çekirdeğinin yanında, aynı depoda bulunur. Yerel
PostgreSQL servisini başlatıp örnek geliştirme ayarlarını yükleyin:

```bash
docker compose -f docker-compose.saas.yml up -d postgres
set -a && . ./.env.example && set +a
python manage.py migrate
python manage.py runserver
```

Tarayıcıdan hesap oluşturduktan sonra temel ürün akışı şöyledir:

1. İsteğe bağlı Etsy OAuth akışı için `PINFORGE_ETSY_KEYSTRING` ve kayıtlı HTTPS
   callback adresini `PINFORGE_ETSY_REDIRECT_URI` olarak ayarlayın. **Etsy
   connection** sayfası PKCE ile hesabı bağlar ve tokenları tenant'a bağlı,
   şifrelenmiş bir kayıtta saklar.
2. **Brand kit** sayfasında mağaza adı, renkler ve paketlenmiş fontları kaydedin.
3. **Add listing** ile Etsy ilan bağlantısını, fiyatı, etiketleri ve 1–5 PNG/JPEG
   ürün görselini özel kataloğa yükleyin.
4. İlan sayfasında beş Pinterest şablonundan birini ve pin metnini seçip yaratıcıyı
   kuyruğa alın.
5. Worker renderı tamamladığında yaratıcı sayfasından 1000 × 1500 PNG'yi indirin.

Production'da OAuth token şifrelemesi için kalıcı bir Fernet anahtarı zorunludur:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Çıktıyı `PINFORGE_TOKEN_ENCRYPTION_KEY` olarak web ve worker proseslerine aynı
secret üzerinden verin. Anahtarı değiştirmek mevcut bağlantı tokenlarını okunamaz
hale getirir; secret deposunda yedekleyin.

Web ve worker ayrı proseslerdir; ikisi aynı PostgreSQL veritabanını ve aynı özel
medya diskini görmelidir:

```bash
# release prosesi
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# web prosesi
python manage.py runserver

# worker prosesi (sürekli)
python manage.py run_jobs

# tanılama veya cron için en fazla bir iş
python manage.py run_jobs --once
```

`PINFORGE_PRIVATE_MEDIA_ROOT` mutlak bir dizin olmalıdır. Bu dizin web sunucusunda
statik/media URL olarak yayınlanmaz; dosyalar yalnız oturum ve tenant kontrolü yapan
indirme görünümünden stream edilir. Kalıcı bir deployment'ta web ve worker için aynı
şifreli volume bağlanmalı ve yedeklenmelidir. Üretim ayarları ayrıca PostgreSQL,
uzun rastgele `PINFORGE_SECRET_KEY`, gerçek `PINFORGE_ALLOWED_HOSTS` ve HTTPS ister.

Bu çalışma ortamında PostgreSQL veya Docker bulunmadığında yalnız hızlı SaaS
testleri açıkça SQLite ile çalıştırılabilir:

```bash
PINFORGE_TEST_SQLITE=1 pytest tests/saas
```

Production ve GitHub CI entegrasyon testleri PostgreSQL kullanır; SQLite bir
deployment seçeneği değildir. Mimari kararlar
[SaaS tasarımında](docs/superpowers/specs/2026-08-02-pinforge-saas-design.md),
ilk uygulama dilimi ise
[SaaS foundation planında](docs/superpowers/plans/2026-08-02-pinforge-saas-foundation.md)
belgelenmiştir. Manuel katalog, dayanıklı render kuyruğu ve özel indirme diliminin
ayrıntıları
[catalog/render planında](docs/superpowers/plans/2026-08-02-pinforge-catalog-render-slice.md)
bulunur.

## Veri güvenliği ve kurtarma

Şeması değiştirilen SQLite dosyasının yanında göç öncesi `.vN.bak` yedeği
oluşturulur. Tokenlar ve API sırları işletim sistemi parola kasasında kalır;
ayarlar, veritabanı ve yedekler desteklenen POSIX sistemlerde yalnız kullanıcı
tarafından okunabilir. Görsel ve AI önbellekleri yaş/boyut sınırlarına göre
temizlenir. `pinforge health` SQLite bütünlüğünü ve kayıt sayılarını hızlıca
kontrol eder.

## Tasarım araçları

Depodaki isteğe bağlı [Open Design](https://github.com/nexu-io/open-design)
entegrasyonu tasarım çıktıları üretmek için kullanılabilir. Kurulum seçenekleri
için [Open Design belgesine](docs/open-design.md) bakın.
