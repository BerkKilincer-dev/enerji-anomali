# Performans raporu

Model sürümü `20261027-2336-2fdb98f`. Bütün sayılar, eğitimde hiç görülmeyen 30 test sayacında
ölçüldü; eşikler ayrı 30 doğrulama sayacında seçildi. Ayrıntılı tablolar ve grafikler:
[reports/ml/results.md](../../reports/ml/results.md).

## Veri

- LEAD 1.0: 200 sayaç, 2016 yılı, saatlik kWh; 1,76 milyon satır. Anomali etiketleri veri
  setinden geliyor.
- Sayaçlar bina bazında bölündü: 140 eğitim, 30 doğrulama, 30 test. Aynı sayaç iki gruba
  girmez, yani test sonuçları yeni bir müşteriye ne olacağını gösterir.
- Hava durumu verisi özellik olarak kullanıldı.

## Sistem nasıl karar veriyor

| Katman | Ne yapar | Neden |
|---|---|---|
| Kurallar | Gece yükü, hafta sonu tüketimi, ani sıçrama, sayaç susması, takılı sayaç | Açıklanabilir; işletmeye "neden" sorusunun cevabını verir |
| Beklenen tüketim modeli (LightGBM) | Her saat için normal tüketimi tahmin eder | İsrafın kWh ve TL karşılığını hesaplar |
| Dedektör (LightGBM) | Etiketli veriden anomali olasılığı | Kuralların kaçırdığı desenleri yakalar |

Bir alarm, bu üçünden biri ateşlediğinde çıkar ve olay olarak birleştirilir (kWh, TL, Türkçe
açıklama). Takılı ya da susan sayaç "veri sorunu" olarak ayrılır; maliyeti yoktur.

## Model karşılaştırması (test sayaçları, saat düzeyi)

| Model | PR-AUC | ROC-AUC | Precision | Recall | F1 | Yanlış alarm / sayaç / hafta |
|---|---|---|---|---|---|---|
| **LightGBM (seçilen)** | **0,77** | **0,97** | 0,74 | 0,66 | 0,70 | 0,17 |
| LightGBM hibrit | 0,77 | 0,96 | 0,82 | 0,65 | 0,73 | 0,12 |
| Random Forest | 0,75 | 0,95 | 0,83 | 0,60 | 0,70 | 0,07 |
| LightGBM, hava durumu yok | 0,73 | 0,94 | 0,60 | 0,68 | 0,64 | 0,37 |
| Isolation Forest | 0,53 | 0,89 | 0,92 | 0,34 | 0,50 | 0,03 |
| Beklenen tüketimden sapma | 0,45 | 0,77 | 0,79 | 0,35 | 0,49 | 0,06 |
| Yalnız kurallar | 0,22 | 0,81 | 0,32 | 0,58 | 0,42 | 0,54 |

- LightGBM'in PR-AUC'si için %95 güven aralığı 0,66–0,90.
- Hava durumu özellikleri PR-AUC'yi 0,73'ten 0,77'ye çıkarıyor.
- Kuralların LEAD etiketlerinde düşük görünmesi beklenen bir durum. LEAD etiketleri çoğunlukla
  sayaç arızası ve düşüş işaretliyor; kurallar ise gece ve hafta sonu israfını arıyor ve bu
  israf LEAD'de etiketli değil. Bu yüzden kurallar aşağıdaki senaryo testleriyle ölçüldü.

## LEAD makalesiyle kıyas (gün düzeyi)

Bizim seçilen modelimiz gün düzeyinde F1 0,63 (precision 0,68, recall 0,59). LEAD makalesindeki
denetimsiz modeller F1 0,40–0,43 aralığında. Ancak makale veriyi gün bazında bölüyor ve test
kümesi anomali yönünden zengin; biz sayaç bazında bölüyoruz. Bu yüzden sayılar doğrudan aynı
şey değil; ayrıntı [lead-benchmark.md](lead-benchmark.md).

## Beklenen tüketim modeli

Normal saatlerde ortalama mutlak hata 9,5 kWh, medyan yüzde hata %7, toplam ağırlıklı hata
(WAPE) %9. İsrafın TL hesabı bu modelin tahmininden yapılıyor.

## İsraf senaryoları

Test sayaçlarına 30'ar yapay israf ve arıza eklendi; bütün sistem (kural + model) şunları buldu:

| Senaryo | Bulunma oranı | Bulma süresi (medyan) |
|---|---|---|
| Gece açık kalan yük | %100 | 6,5 saat |
| Ani sıçrama | %97 | hemen |
| Sayaç susması | %100 | hemen |
| Takılı sayaç | %100 | 5 saat |
| Yavaş kayma (drift) | %57 | 155 saat |

Yavaş kayma, yani tüketimin haftalar içinde yavaş yavaş artması, en zayıf olduğumuz durum.

## Ekipmana göre kurallar

Klima, aydınlatma, PLC ve kompresör mesai dışında kapalı olmalı. Bu ekipmanlarda gece ve hafta
sonu sınırı, aynı saatin geçmiş haftalarına göre 1,5 kattan 1,3 kata indirildi. Soğutma ve genel
sayaçlar 24 saat çalıştığı için 1,5 katta kaldı.

| Ekipman | İsraf olayı (önce → sonra) | TL (önce → sonra) |
|---|---|---|
| Aydınlatma | 315 → 455 | 152 bin → 195 bin |
| Klima | 290 → 449 | 346 bin → 429 bin |
| PLC | 5 → 15 | |
| Kompresör, soğutma, genel | değişmedi | |

Bedeli daha fazla alarm: aydınlatma sayaçlarında haftada ortalama 1,75 alarm. Fazla bulunursa
sınır 1,4'e çekilebilir (`STRICT_OFF_HOURS_RATIO`, `src/models/rules.py`).

## Demo işletmeleri

30 sayaç 5 demo işletmeye dağıtıldı (`data/meter_catalog.csv`). 2016 verisinde bulunan israf:

| İşletme | Tür | Son 90 gün | Son 1 yıl | Açık alarm (1 yıl) |
|---|---|---|---|---|
| Otel | Otel | 16.235 TL | 264.925 TL | 197 |
| Özel Kolej | Eğitim | 42.753 TL | 255.645 TL | 267 |
| Kongre Merkezi | Kamu / etkinlik | 67.036 TL | 202.428 TL | 436 |
| Üniversite Kampüsü | Eğitim | 9.283 TL | 70.173 TL | 122 |
| Ofis Plazası | Ofis | 45.217 TL | 68.718 TL | 144 |

TL hesabı 5,93 TL/kWh varsayılan tarifeyle yapıldı (EPDK ticarethane, tek zamanlı, yüksek kademe,
vergisiz; 4 Nisan 2026 tarifesi). Tarife `.env`'deki `TARIFF_TL_PER_KWH` ile değişir. Her alarmın yanında ekipmana ve anomali
türüne göre hazır bir öneri gösteriliyor (`data/recommendations.csv`).

## Sınırlar

- Veri 2016 yılına ait ve ABD/Avrupa binalarından geliyor; Türkiye'deki gerçek müşteri verisiyle
  yeniden ölçülmeli.
- İsraf için gerçek etiket yok; kurallar yapay senaryolarla ölçüldü. Panelde girilen
  "Uygulandı" ve "Yanlış alarm" durumları ileride eğitim etiketi olarak kullanılıyor
  (`python -m src.models.train --source db`).
- Toplam alarm sayısı sayaç başına haftada yaklaşık 1,6. Bunun büyük kısmı LEAD etiketinde
  olmayan israf alarmı; gerçek kullanımda işletme geri bildirimiyle ayarlanmalı.
- Yavaş kayma geç yakalanıyor.
