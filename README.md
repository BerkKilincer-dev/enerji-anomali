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

Gerekenler: Docker Desktop, Python 3.11+, Node 22.

```bash
cp .env.example .env                      # olduğu gibi çalışır, değiştirmen gerekmez
docker compose up -d --build              # veritabanı, MQTT, API, panel

python -m venv .venv
.venv\Scripts\activate                    # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

python -m src.data.load_db                # LEAD verisini indirir ve yükler (ilk sefer birkaç dk)
python -m src.models.train --publish --no-mlflow   # modeli eğitir, sonuçları yazar (~6 dk)
```

Panel: **http://localhost:5173** · giriş `admin@enerji.local` / `admin-demo-pass`
· API belgeleri: http://localhost:8000/docs

Bilgisayarında kurulu bir PostgreSQL 5432'yi kullanıyorsa `.env` içinde `DB_PORT=55432` yap
ve `DATABASE_URL`'deki portu da `55432` olarak değiştir.

### Canlı demo

Sanal sayaçlar MQTT üzerinden veri gönderir; birine gece israfı eklenir ve panelde görünür:

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
| `anomalies.advice` | Öneri | Claude'un yazdığı öneri (`ANTHROPIC_API_KEY` varsa) |

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
│   │   └── report.py           sonuç tabloları ve grafikler (reports/ml/)
│   ├── scoring/              (ML + Backend) canlı skorlama
│   │   ├── service.py          yeni saatleri 5 dakikada bir skorlar
│   │   └── advice.py           anomaliye Türkçe öneri yazar (öneri API'sinin başlangıcı)
│   ├── ingest/               (Veri + Backend) canlı veri
│   │   ├── mqtt_ingest.py      MQTT'den gelen sayaç okumalarını veritabanına yazar
│   │   └── replay.py           sanal sayaç: LEAD verisini canlıymış gibi gönderir
│   ├── api/                  (Backend) FastAPI
│   │   ├── main.py             tüm uç noktalar (http://localhost:8000/docs)
│   │   ├── repository.py       veritabanı sorguları; her işletme yalnız kendini görür
│   │   ├── schemas.py          istek ve yanıt şekilleri
│   │   ├── auth.py             şifre, oturum, roller
│   │   └── users.py            komut satırından kullanıcı ekleme
│   ├── common/               ortak yardımcılar (veritabanı bağlantısı vb.)
│   └── config.py             .env ayarlarını okur
├── frontend/                 (Frontend) React CRM paneli
│   └── src/
│       ├── App.tsx             giriş kontrolü ve sayfa geçişleri
│       ├── api.ts              API istemcisi; backend şemasıyla aynı tipler
│       ├── i18n.tsx            Türkçe / İngilizce metinler
│       └── components/         Login, Overview (sayaç listesi), BuildingDetail
│                               (sayaç sayfası), EnergyChart, AnomalyTable
├── db/init/001_schema.sql    (Veri + Backend) veritabanı şeması: core, telemetry, ml, ops
├── tests/                    (Herkes) otomatik testler, `pytest -q`
├── notebooks/                (Veri + ML) Colab: 01 veri hattı, 02 ML hattı
├── reports/                  (Veri + ML) veri kalitesi ve model sonuçları, commit edilir
├── docs/                     ayrıntılı rehberler (İngilizce)
├── data/                     indirilen veri; sadece splits.json commit edilir
├── docker/                   MQTT (Mosquitto) ayarı
├── .github/workflows/ci.yml  her push'ta lint, test ve panel derlemesi
├── docker-compose.yml        veritabanı, MQTT, API, panel, skorlama
├── Dockerfile                API ve servislerin Python imajı
├── .env.example              yerel ayarlar; `.env` olarak kopyala
└── requirements*.txt         Python paketleri (dev: test ve ML araçları, colab: notebook)
```

Ayrıntılı rehberler (İngilizce): [Mimari](docs/architecture.md) · [Veri](docs/data.md) ·
[ML](docs/machine-learning.md) · [API](docs/api.md) · [Backend](docs/backend.md) ·
[Frontend](docs/frontend.md)

## Çalışma şekli

`main`'e doğrudan push serbest. Tek kural: başkasının işini bozmamak.

```bash
git pull --rebase                         # işe başlamadan ve push'tan önce
# ... değişiklik ...
pytest -q                                 # Python'a dokunduysan
cd frontend && npm run build              # panele dokunduysan
git add -A && git commit -m "Kısa ve net açıklama"
git push
```

- Push'tan sonra GitHub'daki **Actions** sekmesine bak: kırmızıysa ilk iş onu düzelt.
- `.env`, şifre, API anahtarı, `data/raw`, `data/processed`, `models_out/` asla commit edilmez.
- Veritabanı şemasını veya API alanlarını değiştireceksen önce o alanın sahibine haber ver.
- Herkes aynı bölmeyi (`data/splits.json`) ve seed 42'yi kullanır.
