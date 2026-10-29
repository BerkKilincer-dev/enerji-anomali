# Enerji Anomali CRM

Bir işletmenin (AVM, fabrika, otel, ofis) elektrik sayaçlarını izleyen, israfı ve arızayı
bulan, bulduğu her anomali için işletmeye TL karşılığıyla öneri sunan bir CRM paneli.
SofITo AI eğitimi bitirme projesi (Proje 14).

Veri olarak LEAD 1.0 veri setini kullanıyoruz. Setteki her ID bir **sayaç**; her sayacı bir
işletmeye ve o işletmede çalıştığı yere bağlıyoruz (ör. "Fabrika A, pres hattı, PLC-3 sayacı").

```
sayaç verisi ──► anomali modeli ──► olay + TL maliyeti ──► öneri API'si ──► CRM ekranı
 (LEAD / MQTT)    (kurallar + LightGBM)                     (şablon / Claude)   (işletme görür)
```

## Hızlı başlangıç

Gereken tek şey Docker Desktop. Model eğitmek yok: eğitilmiş model repoda (`models_out/`).
İlk kez kuruyorsan adım adım anlatım ve sorun çözme için **[KURULUM.md](KURULUM.md)**.

```bash
cp .env.example .env                      # olduğu gibi çalışır, değiştirmen gerekmez
docker compose up -d --build              # veritabanı, MQTT, API, panel + otomatik kurulum
docker compose logs -f setup              # ilk açılışta ~1 dk: veri iner, yüklenir, model yayınlanır
```

Panel: **http://localhost:5173** · giriş `admin@enerji.local` / `admin-demo-pass`
· API belgeleri: http://localhost:8000/docs

Demo işletme kullanıcıları (şifre `tenant-demo-pass`); her biri yalnız kendi sayaçlarını görür:
`ozelkolej@enerji.local`, `ofisplazasi@enerji.local`, `otel@enerji.local`,
`kongremerkezi@enerji.local`, `universitekampusu@enerji.local`.

`setup` servisi LEAD verisini indirip veritabanına yükler, 30 sayacı `data/meter_catalog.csv`'deki
5 demo işletmeye dağıtır ve repodaki modelle anomalileri yazar. Sonraki açılışlarda her şeyi yerinde bulur, birkaç saniyede biter.

Bilgisayarında kurulu bir PostgreSQL 5432'yi kullanıyorsa `.env` içinde `DB_PORT=55432` yap
ve `DATABASE_URL`'deki portu da `55432` olarak değiştir.

### Kod yazacaksan (Python)

Python 3.11+ ile bir kere:

```bash
python -m venv .venv
.venv\Scripts\activate                    # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                 # testler
```

PowerShell "running scripts is disabled" derse bir kere:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Modeli yeniden eğitmek (sadece ML geliştirici)

```bash
python -m src.models.train --publish --no-mlflow   # ~6 dk; models_out/ ve reports/ güncellenir
git add models_out reports && git commit -m "Yeni model: ..." && git push
```

Diğerleri `git pull` ve `docker compose up -d` ile yeni modeli alır; `setup` yeni sürümü görüp
yayınlar.

### Canlı demo

Sanal sayaçlar MQTT üzerinden veri gönderir; birine gece israfı eklenir ve panelde görünür
(Python kurulu olmalı):

```bash
docker compose --profile live up -d       # canlı skorlama ve MQTT alıcısı
python -m src.ingest.replay --building 55 91 108 --register --speed 0.05 \
    --inject night_base_load --inject-building 91
```

Skorlama 5 dakikada bir çalışır; en geç o sürede panelde "Demo building (LEAD 91)" sayacında son iki gecenin israf
alarmı çıkar. Durdurmak için `Ctrl+C`, her şeyi kapatmak için `docker compose --profile live down`.

## Kodda isimler

Kod ve veritabanı İngilizce isimler kullanır; panelde Türkçe görünür.

| Kodda | Panelde | Ne |
|---|---|---|
| `building` (`core.buildings`) | Sayaç | Veri setindeki bir ID; saatlik kWh bu kayda bağlı |
| `tenant` (`core.tenants`) | İşletme | AVM, fabrika...; kullanıcılar yalnız kendi işletmesini görür |
| `asset` (`core.assets`) | Makine / hat / bölge | Sayacın bağlı olduğu yer (ör. PLC makinesi) |
| `anomalies.explanation` | Açıklama | Kural tabanlı Türkçe metin, her zaman dolu |
| `recommendation` | Öneri | Ekipman ve anomali türüne göre hazır öneri; metinler `data/recommendations.csv`'de |
| `verdict` | Durum | İletildi (`sent`), Uygulandı (`applied`), Yanlış alarm, Planlı |
| `anomalies.advice` | Ek tavsiye | Claude'un yazdığı tavsiye (`ANTHROPIC_API_KEY` varsa, isteğe bağlı) |

## Dosya yapısı

Bir işe başlarken önce kendi klasörüne bak; parantez içi o klasörün sahibi.

```
enerji-anomali/
├── src/
│   ├── data/                 (Veri) veriyi indirir, hazırlar, veritabanına yükler
│   │   ├── download.py         LEAD ve BDG2 dosyalarını indirir, sağlamasını yapar
│   │   ├── prepare.py          ham veriyi saatlik tablolara çevirir
│   │   ├── split.py            sayaçları train / val / test'e böler (data/splits.json)
│   │   ├── quality.py          veri kalitesi raporu (reports/data/)
│   │   ├── load_db.py          şema, roller, demo admin ve LEAD verisini yükler
│   │   ├── customer.py         sayaç kaydı, CSV içe aktarma, hava durumu
│   │   ├── synthetic.py        yapay anomali senaryoları (gece yükü, sıçrama...)
│   │   └── segments.py         işletme türleri: ofis, AVM, fabrika, otel...
│   ├── features/             (Veri + ML) modelin kullandığı özellikler, tatil takvimi
│   ├── models/               (ML) anomali bulma
│   │   ├── train.py            tüm modelleri eğitir, karşılaştırır, en iyisini kaydeder
│   │   ├── rules.py            kurallar: gece yükü, hafta sonu, sıçrama, takılı sayaç
│   │   ├── detector.py         canlı ve toplu skorlamanın tek giriş noktası
│   │   ├── events.py           alarm saatlerini olaya çevirir: kWh, TL, Türkçe açıklama
│   │   ├── publish.py          repodaki modelle sonuçları veritabanına yazar (eğitimsiz)
│   │   └── report.py           sonuç tabloları ve grafikler (reports/ml/)
│   ├── scoring/              (ML + Backend) canlı skorlama
│   │   ├── service.py          yeni saatleri 5 dakikada bir skorlar
│   │   └── advice.py           isteğe bağlı: Claude ile ek Türkçe tavsiye (ücretli, anahtar gerekir)
│   ├── ingest/               (Veri + Backend) canlı veri
│   │   ├── mqtt_ingest.py      MQTT'den gelen sayaç okumalarını veritabanına yazar
│   │   └── replay.py           sanal sayaç: LEAD verisini canlıymış gibi gönderir
│   ├── api/                  (Backend) FastAPI
│   │   ├── main.py             tüm uç noktalar (http://localhost:8000/docs)
│   │   ├── repository.py       veritabanı sorguları; her işletme yalnız kendini görür
│   │   ├── schemas.py          istek ve yanıt şekilleri
│   │   ├── recommendations.py  ekipman × anomali türüne göre öneri (data/recommendations.csv)
│   │   ├── auth.py             şifre, oturum, roller
│   │   └── users.py            komut satırından kullanıcı ekleme
│   ├── common/               ortak yardımcılar (veritabanı bağlantısı vb.)
│   └── config.py             .env ayarlarını okur
├── dashboard/                (Frontend) Streamlit CRM paneli
│   ├── app.py                  giriş ve sayfalar: işletmeler, genel bakış, tüketim, anomaliler, raporlar
│   ├── api_client.py           API istemcisi (oturum çerezi dahil)
│   ├── charts.py               grafikler
│   ├── reports.py              CSV / Excel / PDF raporları
│   └── style.css               görünüm
├── oldfrontend/              eski React paneli; kullanılmıyor, sadece başvuru için
├── models_out/               (ML) eğitilmiş model; herkes bunu kullanır, eğitim gerekmez
├── db/init/001_schema.sql    (Veri + Backend) veritabanı şeması: core, telemetry, ml, ops
├── tests/                    (Herkes) otomatik testler, `pytest -q`
├── notebooks/                (Veri + ML) Colab: 01 veri hattı, 02 ML hattı
├── reports/                  (Veri + ML) veri kalitesi ve model sonuçları, commit edilir
├── docs/                     ayrıntılı rehberler (İngilizce)
├── data/                     indirilen veri; commit edilenler: splits.json, meter_catalog.csv
│                             (sayaç → işletme) ve recommendations.csv (öneri metinleri)
├── docker/                   MQTT (Mosquitto) ayarı
├── .github/workflows/ci.yml  her push'ta lint, test ve panel derlemesi
├── docker-compose.yml        veritabanı, MQTT, API, panel, otomatik kurulum (setup), skorlama
├── Dockerfile                API ve servislerin Python imajı
├── .env.example              yerel ayarlar; `.env` olarak kopyala
└── requirements*.txt         Python paketleri (dev: test ve ML araçları, colab: notebook)
```

Ayrıntılı rehberler (İngilizce): [Mimari](docs/architecture.md) · [Veri](docs/data.md) ·
[ML](docs/machine-learning.md) · [API](docs/api.md) · [Backend](docs/backend.md) ·
[Panel](docs/dashboard.md)

Modelin başarısı ve demo işletmelerin sonuçları (Türkçe): [Performans raporu](docs/rapor/performans-raporu.md)

## Çalışma şekli

`main`'e doğrudan push serbest. Tek kural: başkasının işini bozmamak.

```bash
git pull --rebase                         # işe başlamadan ve push'tan önce
# ... değişiklik ...
pytest -q                                 # Python'a dokunduysan
docker compose up -d --build             # panele dokunduysan: aç ve dene
git add -A && git commit -m "Kısa ve net açıklama"
git push
```

- Push'tan sonra GitHub'daki **Actions** sekmesine bak: kırmızıysa ilk iş onu düzelt.
- `.env`, şifre, API anahtarı, `data/raw`, `data/processed` asla commit edilmez. `models_out/`
  commit edilir ama sadece ML geliştirici yeni model eğitince.
- Veritabanı şemasını veya API alanlarını değiştireceksen önce o alanın sahibine haber ver.
- Herkes aynı bölmeyi (`data/splits.json`) ve seed 42'yi kullanır.
