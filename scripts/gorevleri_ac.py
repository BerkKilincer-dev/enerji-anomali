"""Görevlendirme dokümanındaki 4 haftalık planı GitHub'a aktarır.

- Kod içeren görevler -> issue (rol etiketi + "Hafta N" milestone'u), projeye eklenir
- Kod içermeyen görevler (Lider, İş geliştirme) -> projede taslak kart

Gereksinim: GitHub CLI giriş yapılmış olmalı ve project izni verilmiş olmalı:
    gh auth login
    gh auth refresh -s project
Kullanım (repo klasöründe):
    python scripts/gorevleri_ac.py --project "Enerji Anomali"
    python scripts/gorevleri_ac.py --project "Enerji Anomali" --dry-run   # sadece listeler
"""

import argparse
import json
import subprocess

KK = "## Kabul kriterleri\n"


def body(amac, kriterler, bagimlilik="-"):
    k = "\n".join(f"- [ ] {x}" for x in kriterler)
    return f"## Amaç\n{amac}\n\n{KK}{k}\n\n## Bağımlılık\n{bagimlilik}\n"


ISSUES = [
    # ---------------- Veri
    (1, "veri", "LEAD verisini indir ve keşif özeti çıkar",
     body("Veriyi tanımak; ekibin geri kalanı bu özetle başlayacak.",
          ["Bina sayısı, satır sayısı, eksik değer oranı, anomali oranı raporlandı",
           "Özet notebooks/01_eda_*.ipynb ve docs/ altında"])),
    (1, "veri", "ASHRAE hava durumu ve bina bilgisini LEAD ile birleştir",
     body("Modele sıcaklık ve bina tipi özelliklerini eklemek.",
          ["Birleştirme script'i src/ altında ve tekrar çalıştırılabilir",
           "Birleşik veri data/ altına parquet olarak yazılıyor"])),
    (1, "veri", "Bina bazlı train/val/test bölmesi oluştur",
     body("Herkesin aynı bölmeyle çalışması ve sızıntının önlenmesi.",
          ["data/splits.json üretildi, aynı bina iki kümede yok",
           "Üretim script'i repoda, random_state=42"])),
    (1, "veri", "Mosquitto ile test mesajı gönder/al",
     body("MQTT altyapısının çalıştığını doğrulamak.",
          ["docker compose ile mqtt ayakta", "Örnek mesaj gönderilip alındı"])),
    (2, "veri", "Sanal sayaç: replay.py geliştir (hız, başlat/durdur)",
     body("Demo ve entegrasyon için canlı veri akışı.",
          ["Aynı saatin tüm binaları birlikte gönderiliyor", "Hız parametresi çalışıyor",
           "Test binalarıyla çalıştırıldı"])),
    (2, "veri", "MQTT dinleyicisi: okumaları veritabanına yaz",
     body("Akan verinin readings tablosuna yazılması.",
          ["Gelen mesajlar readings tablosunda görünüyor", "Eksik okumalar NULL olarak yazılıyor"],
          "Backend ile birlikte")),
    (3, "veri", "Demo için anomali enjeksiyonu",
     body("Sahnede alarmın canlı düşmesini sağlamak.",
          ["Seçilen binada gece tüketimi parametreyle artırılabiliyor",
           "Enjeksiyon komutla açılıp kapanıyor"])),
    (3, "veri", "Tarife varsayımı ve TL kayıp hesabı",
     body("Anomalinin maliyetini göstermek.",
          ["excess_kwh ve est_cost_tl hesaplayan fonksiyon var",
           "Tarife varsayımı .env ve raporda belirtildi"])),
    (4, "veri", "Sentetik anomali senaryoları üret",
     body("Modeli bilinen desenlerle test etmek.",
          ["Ani sıçrama, sıfır okuma, yavaş artış senaryoları var",
           "ML geliştiriciye teslim edildi"])),

    # ---------------- ML
    (1, "ml", "LEAD benchmark notebook'larını çalıştır, referans tabloyu çıkar",
     body("Kendi modellerimizi kıyaslayacağımız tablo.",
          ["Referans skorlar docs/rapor/ altında tablo halinde"])),
    (1, "ml", "Kural motoru: src/models/rules.py",
     body("Anlaşılır baseline ve ürünün kural katmanı.",
          ["Gece taban yükü, hafta sonu, ani sıçrama, sıfır okuma kuralları",
           "Test binalarında PR-AUC/F1 ölçüldü"])),
    (1, "ml", "Random Forest baseline ve MLflow kurulumu",
     body("İlk referans skor ve deney takibi.",
          ["RF varsayılan ayarlarla eğitildi, skor MLflow'da", "MLflow herkes için çalışıyor"])),
    (2, "ml", "Özellik mühendisliği (build_features genişlet)",
     body("Modelin kullanacağı özellikler.",
          ["Saat, gün, sıcaklık, lag ve rolling özellikleri bina bazında",
           "Sızıntı kontrolü yapıldı (gelecek bilgisi yok)"],
          "Veri: birleşik veri ve splits.json")),
    (2, "ml", "Gözetimli LightGBM sınıflandırıcı",
     body("LEAD üzerinde üst sınır referansı.",
          ["Bina bazlı doğrulamayla early stopping", "Skorlar MLflow'da, RF ile karşılaştırıldı"])),
    (3, "ml", "Gözetimsiz model: beklenen tüketim + residual skoru, Isolation Forest",
     body("Etiketsiz müşteride çalışacak asıl yöntem.",
          ["Residual skoru ve Isolation Forest aynı test binalarında ölçüldü"])),
    (3, "ml", "Eşik seçimi ve modelin Backend'e teslimi",
     body("Alarm üretecek skor eşiği ve servis edilebilir model.",
          ["Eşik Lider ile kararlaştırıldı",
           "Model dosyası ve yükleme/tahmin fonksiyonu teslim edildi"])),
    (4, "ml", "evaluate.py ve performans rapor tablosu",
     body("Model performans raporunun sayısal kısmı.",
          ["Tüm modeller aynı bölmede PR-AUC, F1, precision, recall ile tek tabloda",
           "Sentetik senaryo sonuçları eklendi"])),

    # ---------------- Backend
    (1, "backend", "docker compose ile db + mqtt + api ayağa kalkıyor",
     body("Herkesin aynı ortamda çalışması.",
          ["docker compose up hatasız", "/docs sayfası açılıyor", "Şema tabloları oluşmuş"])),
    (2, "backend", "Sahte API uç noktalarını gerçek DB sorgularıyla değiştir",
     body("Frontend'in gerçek veriye geçebilmesi.",
          ["/buildings, /readings, /forecast, /anomalies, /summary DB'den okuyor",
           "Dönen alanlar sözleşmeyle aynı"])),
    (3, "backend", "Skorlama servisi (APScheduler)",
     body("Yeni okumaların periyodik olarak skorlanması.",
          ["Model + kurallar çalışıyor", "Anomaliler anomalies tablosuna yazılıyor"],
          "ML: teslim edilen model")),
    (3, "backend", "LLM ile anomali açıklaması",
     body("Anomaliyi Türkçe öneri metnine çevirmek.",
          ["Sabit prompt şablonu repoda", "explanation alanı doluyor", "API anahtarı .env'de"])),
    (3, "backend", "Telegram alarmı",
     body("Demo için anlık bildirim.",
          ["Yeni anomali Telegram'a mesaj olarak düşüyor"])),
    (4, "backend", "Uçtan uca test ve README güncellemesi",
     body("Demo öncesi sistemin bütün olarak doğrulanması.",
          ["Enjekte edilen anomali 1 dk içinde Telegram'a düşüyor",
           "README'deki kurulum adımları sıfırdan denendi"])),

    # ---------------- Frontend
    (1, "frontend", "React + Vite projesi ve sahte veriyle tüketim grafiği",
     body("Dashboard iskeleti.",
          ["frontend/ altında proje çalışıyor", "Sahte JSON ile grafik çiziliyor"])),
    (2, "frontend", "Sayfalar ve özet kartları",
     body("Genel bakış, bina detayı, anomali listesi, alarm geçmişi.",
          ["Gerçek ve beklenen tüketim aynı grafikte, anomaliler işaretli",
           "Toplam TL kaybı, aktif alarm, en sorunlu bina kartları"])),
    (3, "frontend", "Gerçek API'ye geçiş",
     body("Dashboard'un canlı veriyle çalışması.", ["Tüm sayfalar API'den veri çekiyor"],
          "Backend: gerçek uç noktalar")),
    (4, "frontend", "Mobil görünüm ve kullanıcı testi düzeltmeleri",
     body("Demo kalitesi.", ["Telefonda düzgün görünüyor", "Kullanıcı testi bulguları uygulandı"])),
]

DRAFTS = [
    (1, "Lider", "Şema ve API sözleşmesini ekiple kesinleştir"),
    (1, "Lider", "İlk 2 müşteri görüşmesini ayarla ve yap"),
    (1, "İş geliştirme", "Rakip araştırması (3-5 firma, sunum ve fiyat)"),
    (1, "İş geliştirme", "Görüşme soru listesini son haline getir"),
    (2, "Lider", "2 müşteri görüşmesi daha"),
    (2, "İş geliştirme", "Figma tıklanabilir prototip ve 2-3 kişiyle test"),
    (3, "Lider", "Eşik kararı (ML ile) ve son müşteri görüşmesi"),
    (3, "İş geliştirme", "Gerçek demoyla kullanıcı testi"),
    (3, "İş geliştirme", "Fiyatlandırma ve iş modeli sayfası"),
    (4, "Lider", "Performans raporunun yazımı (ML ile)"),
    (4, "İş geliştirme", "Pitch slaytları (Lider ile)"),
    (4, "Lider", "Pitch provaları ve demo senaryosu"),
]


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True, help="GitHub Project başlığı")
    p.add_argument("--owner", default="@me")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    if a.dry_run:
        for w, lab, t, _ in ISSUES:
            print(f"ISSUE  [Hafta {w}] ({lab}) {t}")
        for w, rol, t in DRAFTS:
            print(f"TASLAK [Hafta {w}] [{rol}] {t}")
        return

    repo = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    existing = json.loads(gh("api", f"repos/{repo}/milestones", "-X", "GET", "-f", "state=all"))
    have = {m["title"] for m in existing}
    for w in range(1, 5):
        if f"Hafta {w}" not in have:
            gh("api", f"repos/{repo}/milestones", "-f", f"title=Hafta {w}")

    projects = json.loads(gh("project", "list", "--owner", a.owner, "--format", "json"))["projects"]
    number = next(str(x["number"]) for x in projects if x["title"] == a.project)

    for w, lab, t, b in ISSUES:
        url = gh("issue", "create", "--title", t, "--body", b, "--label", lab,
                 "--milestone", f"Hafta {w}")
        gh("project", "item-add", number, "--owner", a.owner, "--url", url)
        print("issue:", url)

    for w, rol, t in DRAFTS:
        gh("project", "item-create", number, "--owner", a.owner,
           "--title", f"[Hafta {w}] [{rol}] {t}", "--body", "Kod içermeyen görev (taslak kart).")
        print("taslak:", t)

    print(f"\n{len(ISSUES)} issue ve {len(DRAFTS)} taslak kart oluşturuldu.")


if __name__ == "__main__":
    main()
