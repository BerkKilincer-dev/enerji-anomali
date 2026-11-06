"""Rapor verisi toplama ve CSV / Excel / PDF dışa aktarma (Streamlit'ten bağımsız)."""
import io
from datetime import timedelta

import matplotlib.pyplot as plt
import pandas as pd
from api_client import ApiError
from charts import tr_tarih, tr_tarih_saat
from matplotlib.backends.backend_pdf import PdfPages

KATEGORI = {"waste": "İsraf", "data_issue": "Veri sorunu"}
# API'nin bütün durumları (src/api/schemas.py Verdict); karar verilmemiş alarm "Bekliyor".
DURUM = {
    "sent": "İletildi",
    "applied": "Uygulandı",
    "confirmed": "Onaylandı",
    "false_alarm": "Yanlış alarm",
    "planned": "Planlı",
}


def _tl(x):
    return "₺ " + f"{x:,.0f}".replace(",", ".")


def _kwh(x):
    return f"{x:,.0f}".replace(",", ".") + " kWh"


def collect(api, bina, hours, tariff):
    """bina: {building_id: ad}, tariff: {building_id: TL/kWh}.
    Seçili dönem ve bir önceki dönem için özet döndürür."""
    bina_rows, an_rows = [], []
    toplam_kwh = toplam_onceki = toplam_maliyet = 0.0
    son_zaman = None

    for bid, ad in bina.items():
        try:
            okuma = pd.DataFrame(api.readings(bid, hours * 2))
            anomaliler = api.anomalies(bid, limit=100)
        except ApiError:
            continue
        kwh = onceki = 0.0
        sinir = None
        if not okuma.empty:
            okuma["ts"] = pd.to_datetime(okuma["ts"], utc=True)
            bitis = okuma["ts"].max()
            sinir = bitis - timedelta(hours=hours)
            kwh = float(okuma.loc[okuma["ts"] > sinir, "kwh"].sum())
            onceki = float(okuma.loc[okuma["ts"] <= sinir, "kwh"].sum())
            if son_zaman is None or bitis > son_zaman:
                son_zaman = bitis

        say, tasarruf = 0, 0.0
        if sinir is not None:
            for a in anomaliler:
                bas = pd.to_datetime(a["ts_start"], utc=True)
                if bas <= sinir:
                    continue
                maliyet = a["est_cost_tl"] or 0.0
                say += 1
                tasarruf += maliyet
                an_rows.append(
                    {
                        "Sayaç": ad,
                        "Başlangıç": tr_tarih_saat(bas),
                        "Bitiş": tr_tarih_saat(pd.to_datetime(a["ts_end"], utc=True)),
                        "Tür": KATEGORI.get(a["category"], "Diğer"),
                        "Fazla tüketim (kWh)": round(a["excess_kwh"], 1) if a["excess_kwh"] is not None else None,
                        "Tahmini maliyet (TL)": round(maliyet, 2),
                        "Durum": DURUM.get(a["verdict"], "Bekliyor"),
                        "Açıklama": a["explanation"] or "",
                        "_ts": bas,
                    }
                )
        toplam_kwh += kwh
        toplam_onceki += onceki
        toplam_maliyet += kwh * tariff[bid]
        bina_rows.append(
            {
                "Sayaç": ad,
                "Tüketim (kWh)": round(kwh, 1),
                "Tahmini maliyet (TL)": round(kwh * tariff[bid], 2),
                "Anomali sayısı": say,
                "Tasarruf potansiyeli (TL)": round(tasarruf, 2),
            }
        )

    binalar = pd.DataFrame(
        bina_rows,
        columns=["Sayaç", "Tüketim (kWh)", "Tahmini maliyet (TL)", "Anomali sayısı", "Tasarruf potansiyeli (TL)"],
    )
    if not binalar.empty:
        binalar = binalar.sort_values("Tüketim (kWh)", ascending=False).reset_index(drop=True)
    anom = pd.DataFrame(an_rows)
    if not anom.empty:
        anom = anom.sort_values("_ts", ascending=False).drop(columns="_ts").reset_index(drop=True)
    else:
        anom = pd.DataFrame(
            columns=["Sayaç", "Başlangıç", "Bitiş", "Tür", "Fazla tüketim (kWh)", "Tahmini maliyet (TL)", "Durum", "Açıklama"]
        )

    baslangic = son_zaman - timedelta(hours=hours) if son_zaman is not None else None
    ozet = {
        "kwh": toplam_kwh,
        "kwh_onceki": toplam_onceki,
        "maliyet": toplam_maliyet,
        "anomali": int(binalar["Anomali sayısı"].sum()) if not binalar.empty else 0,
        "tasarruf": float(binalar["Tasarruf potansiyeli (TL)"].sum()) if not binalar.empty else 0.0,
        # Tek tarife varsa o, sayaçlar farklıysa None ("sayaç bazında").
        "tarife": next(iter(set(tariff.values()))) if len(set(tariff.values())) == 1 else None,
        "donem": f"{tr_tarih(baslangic)} – {tr_tarih(son_zaman)}" if son_zaman is not None else "-",
        "bina_sayisi": len(bina),
    }
    return {"ozet": ozet, "binalar": binalar, "anomaliler": anom}


def _ozet_tablosu(o):
    degisim = ""
    if o["kwh_onceki"]:
        p = (o["kwh"] - o["kwh_onceki"]) / o["kwh_onceki"] * 100
        degisim = f"{p:+.1f}%".replace(".", ",")
    return pd.DataFrame(
        [
            ("Dönem", o["donem"]),
            ("Sayaç sayısı", o["bina_sayisi"]),
            ("Toplam tüketim (kWh)", round(o["kwh"], 1)),
            ("Önceki döneme göre değişim", degisim or "-"),
            ("Tahmini maliyet (TL)", round(o["maliyet"], 2)),
            ("Tespit edilen anomali", o["anomali"]),
            ("Tasarruf potansiyeli (TL)", round(o["tasarruf"], 2)),
            ("Tarife (TL/kWh)", o["tarife"] if o["tarife"] is not None else "sayaç bazında"),
        ],
        columns=["Alan", "Değer"],
    )


def to_csv(rapor):
    """Anomali tablosu; Excel'de Türkçe karakterler bozulmasın diye BOM'lu."""
    return rapor["anomaliler"].to_csv(index=False, sep=";").encode("utf-8-sig")


def to_xlsx(rapor):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        _ozet_tablosu(rapor["ozet"]).to_excel(w, sheet_name="Özet", index=False)
        rapor["binalar"].to_excel(w, sheet_name="Sayaçlar", index=False)
        rapor["anomaliler"].to_excel(w, sheet_name="Anomaliler", index=False)
        for ws in w.book.worksheets:
            for kolon in ws.columns:
                en_uzun = max((len(str(c.value)) for c in kolon if c.value is not None), default=8)
                ws.column_dimensions[kolon[0].column_letter].width = min(max(en_uzun + 2, 12), 60)
    return buf.getvalue()


def _kisalt(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def to_pdf(rapor, baslik="Enerji Tüketimi ve Tasarruf Raporu"):
    o, binalar, anom = rapor["ozet"], rapor["binalar"], rapor["anomaliler"]
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # --- Sayfa 1: özet ---
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.95, baslik, fontsize=18, fontweight="bold", color="#0F172A")
        fig.text(0.07, 0.925, f"Dönem: {o['donem']}  ·  {o['bina_sayisi']} sayaç", fontsize=10, color="#475569")

        kutular = [
            ("Toplam tüketim", _kwh(o["kwh"])),
            ("Tahmini maliyet", _tl(o["maliyet"])),
            ("Tespit edilen anomali", str(o["anomali"])),
            ("Tasarruf potansiyeli", _tl(o["tasarruf"])),
        ]
        for i, (et, deger) in enumerate(kutular):
            x = 0.07 + i * 0.225
            fig.patches.append(
                plt.Rectangle((x, 0.83), 0.21, 0.07, transform=fig.transFigure, fc="#F4F7FC", ec="#E2E8F0")
            )
            fig.text(x + 0.012, 0.875, et, fontsize=8, color="#64748B")
            fig.text(x + 0.012, 0.845, deger, fontsize=11.5, fontweight="bold", color="#0F172A")

        ax = fig.add_axes([0.28, 0.46, 0.65, 0.33])
        ilk = binalar.head(8).iloc[::-1]
        if ilk.empty:
            ax.text(0.5, 0.5, "Veri yok", ha="center", va="center", color="#64748B")
            ax.axis("off")
        else:
            ax.barh([_kisalt(b, 26) for b in ilk["Sayaç"]], ilk["Tüketim (kWh)"], color="#2563EB")
            ax.set_title("Sayaç bazlı tüketim (kWh)", loc="left", fontsize=10, color="#0F172A")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            ax.tick_params(labelsize=8, colors="#475569")
            ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}".replace(",", "."))

        fig.text(0.07, 0.40, "Tasarruf fırsatları (en yüksek maliyetli 6 anomali)", fontsize=10, fontweight="bold")
        en_yuksek = anom.sort_values("Tahmini maliyet (TL)", ascending=False).head(6) if not anom.empty else anom
        if en_yuksek.empty:
            fig.text(0.07, 0.37, "Bu dönemde anomali yok.", fontsize=9, color="#64748B")
        else:
            y = 0.37
            for _, r in en_yuksek.iterrows():
                fig.text(0.07, y, f"• {_kisalt(r['Sayaç'], 28)}  {r['Başlangıç']}  —  {_tl(r['Tahmini maliyet (TL)'])}",
                         fontsize=8.5, color="#0F172A")
                y -= 0.025
        tarife = f"{o['tarife']:.2f} TL/kWh" if o["tarife"] is not None else "sayaç bazında"
        fig.text(0.07, 0.04, f"Tarife: {tarife}. Maliyetler tahminidir.", fontsize=7.5, color="#64748B")
        pdf.savefig(fig)
        plt.close(fig)

        # --- Sonraki sayfalar: anomali tablosu (sayfa başına 28 satır, en fazla 6 sayfa) ---
        sutunlar = ["Sayaç", "Başlangıç", "Tür", "Fazla tüketim (kWh)", "Tahmini maliyet (TL)", "Durum"]
        sayfa_boyu = 28
        parcalar = [anom.iloc[i : i + sayfa_boyu] for i in range(0, len(anom), sayfa_boyu)]
        for n, parca in enumerate(parcalar[:6], start=1):
            fig = plt.figure(figsize=(8.27, 11.69))
            fig.text(0.07, 0.95, f"Anomali kayıtları ({n}/{min(len(parcalar), 6)})", fontsize=14, fontweight="bold")
            ax = fig.add_axes([0.05, 0.05, 0.9, 0.86])
            ax.axis("off")
            hucre = [[_kisalt(r[c], 24) if c == "Sayaç" else ("" if pd.isna(r[c]) else r[c]) for c in sutunlar]
                     for _, r in parca.iterrows()]
            tablo = ax.table(cellText=hucre, colLabels=sutunlar, loc="upper center", cellLoc="left")
            tablo.auto_set_font_size(False)
            tablo.set_fontsize(7.5)
            tablo.scale(1, 1.6)
            for (satir, _), h in tablo.get_celld().items():
                h.set_edgecolor("#E2E8F0")
                if satir == 0:
                    h.set_facecolor("#0F172A")
                    h.set_text_props(color="white", fontweight="bold")
            pdf.savefig(fig)
            plt.close(fig)
    return buf.getvalue()