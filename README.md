# Enerji Anomali Tespiti

Ticari binaların elektrik sayacı verisinden anomalileri tespit eden, TL karşılığını hesaplayan
ve tasarruf önerisi sunan sistem. SofITo Yapay Zeka Eğitimi bitirme projesi (Proje 14).

Veri: LEAD — https://www.kaggle.com/competitions/energy-anomaly-detection

## Hızlı başlangıç

```bash
git clone <repo-adresi>
cd enerji-anomali
cp .env.example .env          # değerleri doldurun
docker compose up --build     # veritabanı + MQTT + API
```

- API dokümanı: http://localhost:8000/docs
- Veritabanı: localhost:5432
- MQTT: localhost:1883

Python ortamı (model ve veri çalışmaları için):

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Klasörler

| Klasör | İçerik | Sahibi |
|---|---|---|
| `src/ingest` | MQTT replay, DB yazımı | Veri mühendisi |
| `src/features` | Özellik çıkarma | Veri + ML |
| `src/models` | Eğitim, değerlendirme, kurallar | ML geliştirici |
| `src/api` | FastAPI, skorlama, alarm | Backend |
| `frontend` | React dashboard | Frontend |
| `docs` | Görüşmeler, rapor, pitch | Lider, İş geliştirme |
| `notebooks` | Sadece keşif | Herkes |
| `db/init` | Veritabanı şeması | Veri + Backend |

## Çalışma akışı

```bash
git checkout main && git pull
git checkout -b feature/kisa-aciklama
# ... çalış ...
git add . && git commit -m "Açıklama"
git push -u origin feature/kisa-aciklama
# GitHub'da PR aç, eşin onaylasın
```

- main dalına doğrudan push yok; her değişiklik PR ve eş onayıyla girer.
- PR'lar küçük tutulur (en fazla 1–3 günlük iş).
- `data/`, `.env` ve model dosyaları repoya girmez.
- Herkes aynı bölme listesini (`data/splits.json`) ve `random_state=42` kullanır.
- Her perşembe main dalı sıfırdan çekilip `docker compose up` ile test edilir.

## Dal isimleri

`feature/...` yeni iş · `fix/...` hata düzeltme · `docs/...` doküman
