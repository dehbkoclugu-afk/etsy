# PinForge Python Masaüstü Uygulaması Tasarımı

## Amaç

PinForge, Etsy ürün verilerinden Pinterest için 1000 x 1500 piksel statik pinler üretir. İlk kullanılabilir sürüm, bir ürün klasörünü içe alıp beş görsel şablon oluşturur; başlık ve açıklamaları kullanıcıya düzenletir; PNG dosyalarıyla birlikte Pinterest toplu yüklemeye uygun CSV çıktısı verir.

Başarı ölçütü: Kullanıcı bir ürün klasörü seçtikten sonra 30 saniye içinde beş farklı pin görselini önizleyebilmeli ve tek işlemle dışa aktarabilmelidir.

## Kapsam

İlk kullanılabilir sürüm şunları içerir:

- Windows ve Linux'ta çalışan PySide6 masaüstü arayüzü
- `products.json` ve yerel görsellerden klasör içe aktarma
- Manuel ürün ekleme ve metin düzenleme
- Pillow ile deterministik 1000 x 1500 render
- Mockup Hero, List Stack, Split Compare, Text Overlay ve Grid Preview şablonları
- Marka renkleri, mağaza adı ve iki yerleşik font
- PNG dışa aktarma ve Pinterest uyumlu CSV üretimi
- SQLite tabanlı taslak ve kuyruk kalıcılığı
- Aynı çekirdeği kullanan temel CLI komutları

İlk sürüm dışında kalır:

- Etsy ve Pinterest OAuth/API bağlantıları
- Otomatik Pinterest yayınlama ve arka plan zamanlayıcısı
- Bulut senkronizasyonu, çoklu kullanıcı ve mobil uygulama
- Video veya Idea Pin üretimi
- Yapay zeka metin üretiminin zorunlu olması

API katmanları, çevrimdışı akış kanıtlandıktan sonra ayrı milestone olarak eklenir. Bu sayede uygulama üçüncü taraf onayları olmadan değer üretir.

## Yaklaşım seçenekleri

1. PySide6 + Pillow: En iyi masaüstü deneyimi, güçlü önizleme ve güvenilir batch render sunar. Dağıtım boyutu daha büyüktür. Önerilen yaklaşımdır.
2. Tkinter + Pillow: Daha küçük ve standart kütüphaneye yakın olur; ancak modern iki panelli arayüz, sürükle-bırak ve kaliteli durum yönetimi daha fazla özel kod ister.
3. Yalnızca CLI + Pillow: En kısa uygulamadır; ürün seçme, şablon önizleme ve metin düzeltme hedefini karşılamaz.

Karar: PySide6 + Pillow. Tek render çekirdeği hem GUI hem CLI tarafından kullanılır.

## Mimari

Kod dört belirgin bölüme ayrılır:

- `domain`: Ürün, marka kiti, pin taslağı ve kuyruk durumları. GUI veya dosya sistemine bağımlı değildir.
- `rendering`: Pillow tabanlı render motoru, yazı sığdırma ve beş şablon. Girdi alır, `PIL.Image` veya dosya çıktısı üretir.
- `application`: Klasör içe aktarma, taslak üretme, dışa aktarma ve kuyruk servisleri.
- `ui`: PySide6 ekranları ve view-model bağlantıları. Render kurallarını içermez.

SQLite erişimi küçük bir repository sınıfında tutulur. Ayrı ORM eklenmez; Python'ın yerleşik `sqlite3` modülü yeterlidir. Ağ entegrasyonu eklenene kadar HTTP bağımlılığı da kurulmaz.

## Kullanıcı akışı

1. Kullanıcı bir ürün klasörü seçer.
2. Uygulama `products.json` dosyasını doğrular, göreli görsel yollarını çözer ve ürünleri listeler.
3. Kullanıcı bir ürünü ve üretilecek şablonları seçer.
4. Sağ panelde şablon önizlemeleri oluşur. Başlık, alt başlık, maddeler ve marka ayarları düzenlenebilir.
5. Kullanıcı PNG'leri dışa aktarır veya taslakları kuyruğa ekler.
6. Dışa aktarma klasörüne görseller ve `schedule.csv` yazılır.

## Render sistemi

Her şablon aynı arayüzü uygular ve sabit 1000 x 1500 tuval üretir. Görseller render başlamadan önce açılır ve EXIF yönü düzeltilir. Metin, belirlenen kutuya sığana kadar font boyutunu azaltan ortak bir yardımcıyla çizilir. Minimum boyutta hâlâ sığmayan metin açık hata döndürür; sessizce kırpılmaz.

Yerleşik DejaVu Serif Bold ve DejaVu Sans fontları paketlenir. Font yolu bulunamadığında sistem fontuna sessiz geçiş yapılmaz. Bu, farklı bilgisayarlarda aynı görseli üretmeyi sağlar.

## Veri ve hata yönetimi

`products.json` okuması güven sınırıdır. Zorunlu alanlar, URL biçimi ve görsel dosyalarının varlığı doğrulanır. Hatalar ürün bazında kullanıcıya gösterilir; sağlam ürünlerin içe alınması engellenmez.

Render ve dışa aktarma atomik dosya yazımı kullanır: çıktı önce geçici dosyaya yazılır, sonra hedef ada taşınır. Mevcut dosyaların üzerine yazma kullanıcı seçimine bağlıdır. Veritabanı şema sürümü `PRAGMA user_version` ile takip edilir.

## Arayüz yönü

Arayüz, üretim odaklı iki panelli bir masaüstü çalışma alanıdır. Sol panel ürün ve şablon seçimini, sağ panel 2:3 önizlemeleri ve metin düzenlemeyi taşır. Koyu yeşil ana renk ve krem yüzey markaya uyarlanır; tek vurgu rengi kullanılır. Birincil eylem `PNG ve CSV dışa aktar` olarak açıkça öne çıkar.

Klavye odağı görünür olur, tüm kontroller etiketlenir, tıklama alanları en az 36 piksel tutulur. Boş klasör, bozuk manifest, render devam ediyor, başarı ve kısmi hata durumları ayrı ayrı tasarlanır. Pencere daraldığında önizleme alanı tek sütuna düşer.

## Testler

- Manifest doğrulama ve göreli yol çözme birim testleri
- Her şablon için boyut ve deterministik çıktı testi
- Uzun başlık ve eksik görsel testleri
- CSV sütunları, kaçış kuralları ve UTM üretimi testleri
- SQLite taslak/kuyruk durum geçişi testleri
- GUI açılış ve temel seçim akışı smoke testi

Görsel golden dosyaları yalnızca şablonların yerleşimi sabitlendikten sonra eklenir. İlk testler piksel boyutu, tekrar üretilebilirlik ve kritik bölgelerin boş kalmaması üzerine kurulur.

## Yapım sırası

1. M1: Paket yapısı, domain modelleri, klasör importer, Pillow çekirdeği, Mockup Hero, CLI ve test.
2. M2: Kalan dört şablon, marka kiti ve render dayanıklılığı.
3. M3: PySide6 ürün/generator arayüzü ve canlı önizleme.
4. M4: SQLite kuyruk, PNG/CSV export ve Windows paketleme.
5. M5: İsteğe bağlı yapay zeka metin üretimi.
6. M6: Onay ve kimlik bilgileri sağlanırsa Etsy/Pinterest API entegrasyonu.

M1 gerçek bir örnek ürünle 1000 x 1500 çıktı üretmeden M2'ye geçilmez. API milestone'ları çevrimdışı M4 akışı kullanılabilir olmadan başlatılmaz.

## Kabul ölçütleri

- Temiz kurulumdan sonra uygulama örnek manifesti içe alır.
- Beş şablonun her biri tam 1000 x 1500 görsel üretir.
- Aynı girdi iki çalıştırmada aynı piksel çıktısını verir.
- Uzun metin tuval dışına taşmaz ve sessizce kaybolmaz.
- Dışa aktarılan CSV, üretilen her görselle aynı başlık, açıklama, bağlantı ve dosya adını içerir.
- Uygulama API anahtarı olmadan tamamen kullanılabilir kalır.
