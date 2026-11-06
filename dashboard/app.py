import base64
import csv
import html
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from api_client import API_URL, EVENTS, Api, ApiError
from charts import (
    DURUS_RENK,
    KAPALI,
    KAPALI_MAX_GUN,
    build_events,
    chart_alt,
    closed_spans,
    consumption_chart,
    donut_chart,
    open_spans,
    tr_tarih,
    tr_tarih_saat,
)
from icons import LOGO, sidebar_css, svg

from reports import collect, to_csv, to_pdf, to_xlsx

BRAND = "Wattwise"  # tasarım görselindeki ad: EnerjiVista
BRAND_SUB = "Daha verimli bir yarın"
TARIFF_TL = 5.93  # API sayacın tarifesini vermezse kullanılan varsayılan (TL/kWh)
# İşletme özetinin dönemi: her sayacın son N günü. LEAD verisi 2016 sonunda bitiyor ve
# Aralık sakin geçiyor, bu yüzden demo 90 günle açılır.
DONEMLER = {"30 gün": 30, "90 gün": 90, "1 yıl": 365}
LISTE_LIMIT = 200  # anomaliler sayfasında sekme başına en fazla kayıt

SEGMENT_TR = {
    "office": "Ofis", "mall": "AVM", "retail": "Mağaza", "hotel": "Otel", "hospital": "Hastane",
    "education": "Eğitim", "factory": "Fabrika", "warehouse": "Depo", "data_center": "Veri merkezi",
    "residential": "Konut", "public": "Kamu / etkinlik", "other": "Diğer",
}
# Veri setindeki kullanım türleri (BDG2 / LEAD) İngilizce gelir; panelde Türkçe gösterilir.
KULLANIM_TR = {
    "Office": "Ofis", "Education": "Eğitim", "Entertainment/public assembly": "Eğlence / toplantı",
    "Food sales and service": "Yiyecek satışı ve servis", "Manufacturing/industrial": "Üretim / sanayi",
    "Lodging/residential": "Konaklama / konut", "Healthcare": "Sağlık", "Public services": "Kamu hizmeti",
    "Religious worship": "İbadet yeri", "Parking": "Otopark", "Services": "Hizmet", "Other": "Diğer",
}
EKIPMAN_TR = {
    "klima": "Klima", "kompresor": "Kompresör", "aydinlatma": "Aydınlatma",
    "sogutma": "Soğutma", "plc": "PLC / makine", "genel": "Genel tüketim",
}
KAT = {"waste": ("İsraf", "mid"), "data_issue": ("Veri sorunu", "data")}
# Öneri durumu ve alarm kararı; API'de ikisi de "verdict".
VER = {
    "sent": ("İletildi", "blue"), "applied": ("Uygulandı", "low"),
    "false_alarm": ("Yanlış alarm", "data"), "planned": ("Planlı", "blue"),
    "confirmed": ("Onaylandı", "low"),
}
KAPALI_DURUM = {"applied", "false_alarm", "planned"}  # işletmeden başka bir şey beklenmeyen durumlar
# Durum düğmeleri: (kod, etiket, simge). Renkleri style.css'te düğme anahtarının başına göre verilir.
KARAR = [
    ("sent", "İletildi", ":material/send:"),
    ("applied", "Uygulandı", ":material/check_circle:"),
    ("false_alarm", "Yanlış alarm", ":material/block:"),
    ("planned", "Planlı", ":material/event_available:"),
]
DURUM_FILTRE = {
    "Tümü": None, "Açık": "open", "Yeni": "new", "İletildi": "sent", "Uygulandı": "applied",
    "Yanlış alarm": "false_alarm", "Planlı": "planned",
}

st.set_page_config(page_title=BRAND, page_icon=str(Path(__file__).parent / "brand" / "logo.png"), layout="wide")
css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def fig_html(fig, kind="donut", alt=""):
    """Matplotlib şeklini ortalanmış, gömülü bir görsele çevirir (Streamlit sarmalayıcılarından bağımsız).

    alt: ekran okuyucunun okuyacağı özet; st.pyplot alternatif metin veremediği için grafikler buradan çizilir.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'<div class="{kind}-wrap"><img src="data:image/png;base64,{b64}" alt="{html.escape(alt)}"></div>'


# Tablolardaki sayılar tarayıcının diline göre biçimlenir (Türkçe: 114.443,14).
SAYI_SUTUN = {
    ad: st.column_config.NumberColumn(format="localized")
    for ad in ("Tüketim (kWh)", "Tahmini maliyet (TL)", "Tasarruf potansiyeli (TL)", "Fazla tüketim (kWh)",
               "Toplam (kWh)", "Saatlik ort. (kWh)")
}


def num(x, d=0):
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def tl(x):
    return "-" if x is None else "₺ " + num(x)


def delta_html(cur, prev, etiket):
    if not prev:
        return "<span>Önceki dönem verisi yok</span>"
    p = (cur - prev) / prev * 100
    cls = "up" if p > 0 else "down"
    arrow = "↑" if p > 0 else "↓"
    return f'<span class="{cls}">{arrow} %{num(abs(p), 1)}</span> <span>({etiket})</span>'


def rec_html(a):
    r = a.get("recommendation")
    if not r:
        return ""
    return (f'<div class="rec"><div class="rec-t">{svg("bulb", 17)}<span>{html.escape(r["title"])}</span></div>'
            f'<div class="rec-a">{html.escape(r["action"])}</div></div>')


def verdict_badge(a):
    v = VER.get(a["verdict"])
    return f'<span class="badge sev-{v[1]}">{v[0]}</span>' if v else '<span class="badge sev-wait">Yeni</span>'


def kpi_card(icon, tone, title, value, sub):
    return (
        f'<div class="kpi"><div class="kpi-icon {tone}">{icon}</div>'
        f'<div class="kpi-title">{title}</div><div class="kpi-value">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>'
    )


def severity(pct, cost):
    # VARSAYIM: eşikler ekiple netleştirilebilir.
    if pct is not None:
        return "Yüksek" if pct >= 100 else "Orta" if pct >= 60 else "Düşük"
    c = cost or 0
    return "Yüksek" if c >= 30 else "Orta" if c >= 15 else "Düşük"


def anomaly_panel(items, bina, pct_by_id):
    if not items:
        body = '<div class="an-empty">Bu sayaç için anomali yok.</div>'
    else:
        rows = []
        for a in items:
            pct = pct_by_id.get(a["id"])
            if a["category"] == "data_issue":
                tone, ad = "data", "Veri sorunu"
            else:
                ad = severity(pct, a["est_cost_tl"])
                tone = {"Yüksek": "high", "Orta": "mid", "Düşük": "low"}[ad]
            if pct is not None and a["category"] != "data_issue":
                desc = f"Beklenen değerin %{pct:.0f} üzerinde tüketim tespit edildi."
            else:
                desc = (a["explanation"] or "Beklenenden yüksek tüketim tespit edildi.")[:110]
            zaman = tr_tarih_saat(pd.to_datetime(a["ts_start"], utc=True))
            rows.append(
                f'<div class="an-item"><span class="an-dot sev-{tone}"></span><div class="an-body">'
                f'<div class="an-top"><span class="badge sev-{tone}">{ad}</span>'
                f'<span class="an-time">{zaman}</span></div>'
                f'<div class="an-name">{html.escape(bina)}</div>'
                f'<div class="an-desc">{html.escape(desc)}</div>'
                + (f'<div class="an-rec">{svg("bulb", 15)}<span>{html.escape(a["recommendation"]["title"])}</span></div>'
                   if a.get("recommendation") else "")
                + f'<div class="an-cost">Tahmini maliyet etkisi: <b>{tl(a["est_cost_tl"])}</b></div>'
                f'</div><span class="an-chev">{svg("chevron-right", 16, "#94A3B8")}</span></div>'
            )
        body = "".join(rows)
    return (
        f'<div class="an-panel"><div class="an-head"><span class="an-title">{svg("bell", 20, "#EF4444")}'
        'Son Anomaliler</span></div>'
        f'<div class="an-list">{body}</div></div>'
    )


def legend_rows(items):
    return "".join(
        f'<div class="lg-row"><i class="dot" style="background:{c}"></i>'
        f'<span class="lg-n">{html.escape(n)}</span><span class="lg-v">{v}</span></div>'
        for c, n, v in items
    )


def bar_rows(items):
    return "".join(
        f'<div class="br"><div class="br-top"><span class="br-n">{html.escape(n)}</span>'
        f'<span class="br-v">{num(kwh)} kWh<em>%{num(pct)}</em></span></div>'
        f'<div class="br-bar"><i style="width:{w:.1f}%;background:{c}"></i></div></div>'
        for c, n, kwh, w, pct in items
    )


GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
DURUS = {"holiday": ("Tatil", "blue"), "maintenance": ("Bakım", "mid"), "shutdown": ("Duruş", "data")}


def _vardiya(gunler, *araliklar):
    return [(g, dtime.fromisoformat(a), dtime.fromisoformat(k)) for g in gunler for a, k in araliklar]


SABLON = {
    "Hafta içi 08:00–18:00": _vardiya(range(1, 6), ("08:00", "18:00")),
    "Hafta içi 2 vardiya (06:00–22:00)": _vardiya(range(1, 6), ("06:00", "14:00"), ("14:00", "22:00")),
    "Pzt–Cmt 2 vardiya (06:00–22:00)": _vardiya(range(1, 7), ("06:00", "14:00"), ("14:00", "22:00")),
    "7/24, 3 vardiya": _vardiya(range(1, 8), ("00:00", "08:00"), ("08:00", "16:00"), ("16:00", "00:00")),
    "Boş takvim": [],
}


def saatleri_dogrula(df):
    """Tablodaki satırları API biçimine çevirir; (hata, periyotlar) döner."""
    satirlar = df.dropna(how="all")
    if satirlar.isna().any().any():
        return "Her satırda gün, açılış ve kapanış dolu olmalı.", None
    def hhmm(v):
        # Boş başlayan tabloda yeni satırlar metin ("18:25:00") döner, şablondan gelenler time nesnesi.
        if isinstance(v, str):
            v = dtime.fromisoformat(v.strip())
        return f"{v:%H:%M}"

    try:
        periyot = [
            {"weekday": GUNLER.index(r["Gün"]) + 1, "opens": hhmm(r["Açılış"]), "closes": hhmm(r["Kapanış"])}
            for _, r in satirlar.iterrows()
        ]
    except ValueError:
        return "Saatler SS:DD biçiminde olmalı (örn. 08:00).", None

    # API ile aynı kural: haftalık çemberde saniye; her periyot bir sonrakinin başlangıcından önce bitmeli.
    def sn(hhmm):
        return int(hhmm[:2]) * 3600 + int(hhmm[3:]) * 60

    gun, hafta = 86_400, 7 * 86_400
    sirali = sorted(
        (((p["weekday"] - 1) * gun + sn(p["opens"]), (sn(p["closes"]) - sn(p["opens"])) % gun or gun, p) for p in periyot),
        key=lambda t: t[:2],
    )
    for i, (bas, sure, p) in enumerate(sirali):
        sonraki, _, q = sirali[(i + 1) % len(sirali)]
        if bas + sure > sonraki + (hafta if i + 1 == len(sirali) else 0):
            return (
                f"{GUNLER[p['weekday'] - 1]} {p['opens']}–{p['closes']} vardiyası, "
                f"{GUNLER[q['weekday'] - 1]} {q['opens']}–{q['closes']} ile çakışıyor."
            ), None
    return None, periyot

def takvim_bantlari(api, bid, bas, bit):
    """Grafik penceresi için açık vardiyalar, çalışma dışı ve planlı duruş bantları.

    API hata verirse grafik bantsız çizilsin diye boş döner.
    """
    try:
        saatler = api.operating_hours(bid)
        duruslar = api.downtime(bid)
    except ApiError:
        return None, None, []
    acik = open_spans(saatler, bas, bit) if saatler else None
    kapali = closed_spans(acik, bas, bit) if acik is not None and bit - bas <= timedelta(days=KAPALI_MAX_GUN) else None
    ds = []
    for d in duruslar:
        # Okumalarla aynı kural: bina yerel saati, UTC etiketli; kaydırma yok.
        s, e = pd.Timestamp(d["starts_at"], tz="UTC"), pd.Timestamp(d["ends_at"], tz="UTC")
        if e > bas and s < bit:
            ds.append({"start": max(s, bas), "end": min(e, bit), "kind": d["kind"]})
    return acik, kapali, ds


def bant_lejant(kapali, duruslar):
    parts = [f'<span><i class="sw" style="background:{KAPALI}"></i>Çalışma saatleri dışı</span>'] if kapali else []
    for kind in dict.fromkeys(d["kind"] for d in duruslar):
        ad, renk = DURUS_RENK.get(kind, (kind, "#64748B"))
        parts.append(f'<span><i class="sw hatch" style="--c:{renk}"></i>Planlı {ad.lower()}</span>')
    return "".join(parts)


def grafik_tablosu(df, fc):
    """Grafiğin metin alternatifi: aynı veriyi klavye ve ekran okuyucuyla gezilebilen tablo olarak verir."""
    with st.expander("Grafiği tablo olarak göster", icon=":material/table_view:"):
        t = df[["ts", "kwh"]].copy()
        if fc is not None and not fc.empty:
            t = t.merge(fc.groupby("ts")["expected_kwh"].mean().reset_index(), on="ts", how="left")
        t["ts"] = t["ts"].map(tr_tarih_saat)
        st.dataframe(
            t.rename(columns={"ts": "Zaman", "kwh": "Gerçekleşen (kWh)", "expected_kwh": "Beklenen (kWh)"}),
            hide_index=True, use_container_width=True, height=260,
        )


def tarihsiz(metin):
    """Açıklamanın başındaki "2016-12-30 01:00-06:00:" (ya da günler süren "…-2017-01-02 00:00:") tarih
    önekini atar; kartlarda başlangıç ve bitiş zaten Türkçe tarihle ayrıca yazılı."""
    return re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(-(\d{4}-\d{2}-\d{2} )?\d{2}:\d{2})?:\s*", "", metin or "")


def kisa_aciklama(metin, n=110):
    """Bildirim için kısa açıklama: tarih öneki atılır, uzunsa kısaltılır."""
    metin = tarihsiz(metin) or "Beklenmeyen tüketim"
    return metin if len(metin) <= n else metin[: n - 1].rstrip() + "…"


def mesai_disi(df, acik):
    """Çalışma saatleri dışındaki tüketim (kWh, toplam içindeki pay); takvim yoksa None."""
    if not acik or df.empty:
        return None
    icerde = pd.Series(False, index=df.index)
    for s, e in acik:
        icerde |= (df["ts"] >= s) & (df["ts"] < e)
    toplam = float(df["kwh"].sum())
    dis = float(df.loc[~icerde, "kwh"].sum())
    return dis, (dis / toplam * 100 if toplam else 0.0)


def readings_paralel(_api, ids, hours):
    """Birden çok binanın okumalarını aynı anda çeker; hata veren bina atlanır. {bina: satırlar}"""

    def oku(i):
        try:
            return i, _api.readings(i, hours)
        except ApiError:
            return i, None

    # Sırayla 20 istek ~1 sn sürüyordu; API'yi boğmamak için en fazla 6 eşzamanlı istek.
    with ThreadPoolExecutor(max_workers=6) as ex:
        return {i: rows for i, rows in ex.map(oku, ids) if rows is not None}


@st.cache_data(ttl=600, show_spinner="Sayaçlar yükleniyor...")
def kwh_by_building(_api, user_id, ids, hours):
    # user_id önbellek anahtarında: farklı kullanıcıların verisi birbirine karışmasın.
    return {i: sum((r["kwh"] or 0) for r in rows) for i, rows in readings_paralel(_api, ids, hours).items()}

@st.cache_data(ttl=300, show_spinner="Rapor hazırlanıyor...")
def report_data(_api, user_id, items, hours, tariffs):
    # user_id önbellek anahtarında: farklı kullanıcıların verisi karışmasın.
    return collect(_api, dict(items), hours, dict(tariffs))

@st.cache_data(ttl=300, show_spinner="Karşılaştırma verisi yükleniyor...")
def readings_multi(_api, user_id, ids, hours):
    # user_id önbellek anahtarında: farklı kullanıcıların verisi karışmasın.
    out = {}
    for i, rows in readings_paralel(_api, ids, hours).items():
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        out[i] = df[["ts", "kwh"]]
    return out

# ---------- Giriş ----------
if "api" not in st.session_state:
    st.session_state.api = Api()
elif not isinstance(st.session_state.api, Api):
    # api_client.py değişince Streamlit modülü yeniden yükler; eski nesneyi
    # yeni sınıfa bağla ki yeni metotlar görünsün ve oturum cookie'si kalsın.
    st.session_state.api.__class__ = Api
api = st.session_state.api
api._state()
api.reset_stats()  # durum çubuğundaki çağrı sayacı bu çalıştırmaya ait olsun
sayfa_t0 = time.perf_counter()

if "me" not in st.session_state:
    # Tanıtım sayfası: Wattwise'ı tek ekranda anlatır; giriş sağ üstteki "Giriş yap" penceresinden yapılır.
    st.markdown(
        """<style>
        .stApp{background:radial-gradient(1100px 600px at 0% -10%,rgba(59,130,246,.28),transparent 60%),
               radial-gradient(900px 520px at 100% 110%,rgba(74,222,128,.16),transparent 60%),
               linear-gradient(160deg,#0B1222 0%,#111C3A 55%,#172554 100%);}
        .stApp::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.55;
            background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),
                             linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);
            background-size:36px 36px;mask-image:linear-gradient(180deg,#000 0%,transparent 70%);
            -webkit-mask-image:linear-gradient(180deg,#000 0%,transparent 70%);}
        [data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="collapsedControl"],
        [data-testid="stHeader"]{display:none !important;}
        .block-container{zoom:1 !important;max-width:1240px !important;padding:22px 32px 18px !important;}
        .block-container > [data-testid="stVerticalBlock"]{gap:0 !important;}
        /* İlk ekran (üst çubuk, tanıtım, özellikler) pencereyi tam doldurur; fiyatlar kaydırınca altta görünür */
        .st-key-lp_ekran{min-height:calc(100vh - 40px);gap:0 !important;}
        /* Tanıtım bölümü üst çubuk ile özellik kartları arasındaki boşluğu doldurur, içerik dikeyde ortalanır */
        [data-testid="stLayoutWrapper"]:has(> .st-key-lp_hero){flex:1 1 auto !important;display:flex;flex-direction:column;}
        .st-key-lp_hero{flex:1 1 auto !important;justify-content:center !important;padding:24px 0;}

        /* Üst çubuk */
        .lp-brand{display:flex;align-items:center;gap:12px;color:#fff;}
        .lp-brand img{width:50px !important;height:50px !important;}
        .lp-brand b{display:block;font-size:22px;font-weight:700;letter-spacing:-.3px;line-height:1.1;}
        .lp-brand span{display:block;font-size:12.5px;color:#94A3B8;margin-top:2px;}
        .lp-status{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600;color:#A7F3D0;
            background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.35);padding:6px 12px;border-radius:999px;
            white-space:nowrap;}
        .lp-status i{width:8px;height:8px;border-radius:50%;background:#34D399;box-shadow:0 0 0 3px rgba(52,211,153,.2);}
        .lp-status.off{color:#FECACA;background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35);}
        .lp-status.off i{background:#F87171;box-shadow:0 0 0 3px rgba(248,113,113,.2);}
        .st-key-lp_giris button{min-height:44px;padding:0 20px;border:0;border-radius:12px;color:#fff;
            background:linear-gradient(135deg,#2563EB,#1D4ED8);box-shadow:0 10px 24px rgba(37,99,235,.35);}
        .st-key-lp_giris button p{font-size:15px;font-weight:600;}
        .st-key-lp_giris button:hover,.st-key-lp_giris button:focus{color:#fff;filter:brightness(1.1);}
        .st-key-lp_giris button:focus-visible{outline:3px solid #93C5FD;outline-offset:2px;}

        /* Tanıtım */
        .lp-eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600;letter-spacing:.6px;
            text-transform:uppercase;color:#93C5FD;margin-bottom:16px;}
        .lp-hero h1{font-size:44px;line-height:1.1;font-weight:800;letter-spacing:-1.2px;margin:0 0 16px;color:#fff;padding:0;}
        .lp-hero h1 em{font-style:normal;color:#4ADE80;}
        .lp-hero p{font-size:16px;line-height:1.65;color:#CBD5E1;margin:0;max-width:540px;}
        .lp-flow{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:26px;list-style:none;padding:0;}
        .lp-flow li{display:inline-flex;align-items:center;gap:8px;margin:0;font-size:13.5px;font-weight:600;color:#E2E8F0;
            background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);padding:8px 12px;border-radius:10px;}
        .lp-flow li.ok{color:#BBF7D0;border-color:rgba(74,222,128,.35);background:rgba(74,222,128,.10);}
        .lp-flow li + li{margin-left:22px;position:relative;}
        .lp-flow li + li::before{content:"→";position:absolute;left:-20px;color:#64748B;font-size:15px;}

        .lp-card{padding:26px 26px 20px;border-radius:20px;background:rgba(255,255,255,.06);
            border:1px solid rgba(255,255,255,.12);box-shadow:0 24px 50px rgba(0,0,0,.30);color:#fff;}
        .lp-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;}
        .lp-k{font-size:13px;color:#94A3B8;text-transform:uppercase;letter-spacing:.6px;font-weight:600;}
        .lp-v{font-size:32px;font-weight:700;margin-top:6px;letter-spacing:-.6px;}
        .lp-v small{font-size:15px;font-weight:600;color:#94A3B8;margin-left:5px;}
        .lp-pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;padding:6px 12px;
            border-radius:999px;background:rgba(248,113,113,.16);color:#FCA5A5;border:1px solid rgba(248,113,113,.30);
            white-space:nowrap;}
        .lp-pill i{width:7px;height:7px;border-radius:50%;background:#F87171;box-shadow:0 0 0 3px rgba(248,113,113,.25);}
        .lp-card svg.spark{display:block;width:100%;height:auto;aspect-ratio:320/154;margin-top:18px;overflow:visible;}
        .lp-leg{display:flex;gap:18px;font-size:13px;color:#94A3B8;margin-top:12px;}
        .lp-leg span{display:inline-flex;align-items:center;gap:6px;}
        .lp-leg .a{width:14px;border-top:2px solid #60A5FA;}
        .lp-leg .b{width:14px;border-top:2px dashed #94A3B8;}

        .lp-feats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;}
        .lp-feat{padding:16px 16px 14px;border-radius:14px;background:rgba(255,255,255,.05);
            border:1px solid rgba(255,255,255,.10);}
        .lp-feat i{width:34px;height:34px;border-radius:10px;background:rgba(96,165,250,.16);
            border:1px solid rgba(96,165,250,.25);display:inline-flex;align-items:center;justify-content:center;}
        .lp-feat b{display:block;font-size:14.5px;color:#fff;margin:10px 0 4px;}
        .lp-feat span{display:block;font-size:13px;line-height:1.5;color:#94A3B8;}
        .lp-foot{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;font-size:12.5px;color:#94A3B8;
            margin-top:40px;padding-top:14px;border-top:1px solid rgba(255,255,255,.08);}

        /* Fiyatlandırma */
        .lp-fiyat{margin-top:72px;}
        .lp-fiyat-bas{text-align:center;max-width:640px;margin:0 auto 32px;}
        .lp-fiyat-bas h2{font-size:32px;line-height:1.2;font-weight:800;letter-spacing:-.8px;color:#fff;margin:0 0 10px;padding:0;}
        .lp-fiyat-bas p{font-size:15.5px;line-height:1.6;color:#CBD5E1;margin:0;}
        /* Paket kartları: her biri bir Streamlit kapsayıcısı (düğme içinde olsun diye); üzerine gelince öne çıkar */
        [data-testid="stHorizontalBlock"]:has([class*="st-key-lp_plan_"]){gap:18px !important;align-items:stretch !important;}
        [data-testid="stColumn"]:has(> [data-testid="stVerticalBlock"] [class*="st-key-lp_plan_"]) > [data-testid="stVerticalBlock"],
        [data-testid="stLayoutWrapper"]:has(> [class*="st-key-lp_plan_"]){height:100%;}
        [class*="st-key-lp_plan_"]{height:100%;padding:24px 22px 22px;border-radius:18px;color:#fff;gap:0 !important;
            background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);
            transition:transform .2s ease, border-color .2s ease, box-shadow .2s ease, background .2s ease;}
        [class*="st-key-lp_plan_"]:hover,[class*="st-key-lp_plan_"]:focus-within{transform:translateY(-6px);
            background:rgba(255,255,255,.08);border-color:rgba(96,165,250,.75);
            box-shadow:0 28px 60px rgba(37,99,235,.28),0 0 0 1px rgba(96,165,250,.35);}
        .st-key-lp_plan_pro{background:linear-gradient(180deg,rgba(37,99,235,.24),rgba(37,99,235,.08));
            border:1.5px solid rgba(96,165,250,.6);box-shadow:0 24px 50px rgba(37,99,235,.20);}
        .st-key-lp_plan_pro:hover,.st-key-lp_plan_pro:focus-within{background:linear-gradient(180deg,rgba(37,99,235,.32),rgba(37,99,235,.12));}
        [class*="st-key-lp_plan_"] [data-testid="stElementContainer"]:has(.stButton){margin-top:auto;padding-top:22px;}
        [class*="st-key-lp_plan_"] .stButton button{min-height:46px;border-radius:12px;color:#fff;
            background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.28);transition:background .15s, border-color .15s;}
        [class*="st-key-lp_plan_"] .stButton button p{font-size:15px;font-weight:600;}
        [class*="st-key-lp_plan_"] .stButton button:hover{background:rgba(255,255,255,.14);border-color:#fff;color:#fff;}
        [class*="st-key-lp_plan_"] .stButton button:focus-visible{outline:3px solid #93C5FD;outline-offset:2px;}
        .st-key-lp_plan_pro .stButton button{border:0;background:linear-gradient(135deg,#2563EB,#1D4ED8);
            box-shadow:0 10px 24px rgba(37,99,235,.35);}
        .st-key-lp_plan_pro .stButton button:hover{background:linear-gradient(135deg,#2563EB,#1D4ED8);filter:brightness(1.12);}
        .lp-plan-ust{display:flex;justify-content:space-between;align-items:center;gap:10px;}
        .lp-rozet{font-size:12px;font-weight:700;color:#fff;background:#2563EB;padding:4px 10px;border-radius:999px;}
        [class*="st-key-lp_plan_"] h3{font-size:19px;font-weight:700;margin:0;padding:0;color:#fff;}
        .lp-kim{font-size:13.5px;color:#94A3B8;margin-top:4px;}
        .lp-ucret{margin:18px 0 4px;font-size:36px;font-weight:800;letter-spacing:-1px;line-height:1.1;}
        .lp-ucret small{font-size:14px;font-weight:600;color:#94A3B8;letter-spacing:0;margin-left:4px;}
        .lp-kapsam{font-size:13.5px;font-weight:600;color:#93C5FD;padding-bottom:16px;margin-bottom:16px;
            border-bottom:1px solid rgba(255,255,255,.10);}
        .lp-maddeler{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;}
        .lp-maddeler li{display:flex;gap:10px;align-items:flex-start;font-size:14px;line-height:1.45;color:#E2E8F0;margin:0;}
        .lp-maddeler li svg{flex:none;margin-top:2px;}
        .lp-notlar{display:flex;flex-wrap:wrap;justify-content:center;gap:10px 26px;margin-top:26px;
            font-size:13px;color:#94A3B8;}
        .lp-notlar span{display:inline-flex;align-items:center;gap:7px;}

        /* Giriş penceresi */
        [data-testid="stPopoverBody"]{width:380px;max-width:calc(100vw - 32px);padding:22px 22px 16px !important;
            border-radius:16px !important;}
        [data-testid="stPopoverBody"] [data-testid="stForm"]{border:0 !important;padding:0 !important;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"]{
            border:1.5px solid #CBD5E1 !important;border-radius:12px !important;background:#F8FAFC !important;
            min-height:48px;transition:border-color .15s, box-shadow .15s, background .15s;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"]:hover{border-color:#94A3B8 !important;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"]:focus-within{
            border-color:#2563EB !important;background:#fff !important;box-shadow:0 0 0 4px rgba(37,99,235,.14);}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] *{background:transparent !important;border:0 !important;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] input{font-size:15px;color:#0F172A;padding-left:4px;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] input:focus-visible{outline:none !important;}
        [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] [data-testid="stIconMaterial"]{color:#64748B;}
        [data-testid="stPopoverBody"] label p{font-size:13.5px;font-weight:600;color:#334155;}
        [data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"]{margin-top:6px;}
        [data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] button{
            min-height:48px;border:0;border-radius:12px;color:#fff;
            background:linear-gradient(135deg,#2563EB,#1D4ED8);box-shadow:0 10px 24px rgba(37,99,235,.28);}
        [data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] button p{font-size:15.5px;font-weight:600;}
        [data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] button:hover{filter:brightness(1.07);color:#fff;}
        .lg-title{font-size:22px;font-weight:800;letter-spacing:-.4px;color:#0F172A;margin-bottom:4px;line-height:1.2;}
        .lg-sub{font-size:14px;color:#64748B;margin-bottom:14px;}
        .lg-secure{display:flex;align-items:center;gap:10px;margin-top:14px;padding-top:14px;border-top:1px solid #EEF2F7;
            font-size:12.5px;color:#64748B;line-height:1.45;}
        .lg-secure i{width:30px;height:30px;border-radius:9px;background:#EFF6FF;color:#2563EB;flex:none;
            display:inline-flex;align-items:center;justify-content:center;}
        .lg-foot{text-align:center;font-size:12px;color:#64748B;margin-top:12px;}

        @media (max-width:1100px){.lp-hero h1{font-size:36px;}.lp-feats{grid-template-columns:repeat(2,1fr);}}
        @media (prefers-reduced-motion: reduce){[class*="st-key-lp_plan_"]:hover,[class*="st-key-lp_plan_"]:focus-within{transform:none;}}
        @media (max-width:768px){
            .st-key-lp_ekran{min-height:0;}
            .lp-fiyat{margin-top:48px;}
            .lp-fiyat-bas h2{font-size:26px;}
            .block-container{padding:16px 16px 20px !important;}
            .lp-brand span,.lp-status{display:none;}
            .lp-hero h1{font-size:28px;}
            .lp-hero p{font-size:15px;}
            .lp-feats{grid-template-columns:1fr;}
        }
        </style>""",
        unsafe_allow_html=True,
    )

    try:
        api_acik = bool(api.health())
    except ApiError:
        api_acik = False

    def _ozellik(ikon, baslik, metin):
        return f'<div class="lp-feat"><i>{svg(ikon, 18, "#93C5FD")}</i><b>{baslik}</b><span>{metin}</span></div>'

    # Ürün önizlemesi: gerçek veri değil, temsili bir çizim; ekran okuyucudan gizli.
    egri = (
        "M0 70 L20 66 L40 68 L60 58 L80 60 L100 50 L120 54 L140 46 L160 52 L180 48 L200 40 "
        "L210 18 L222 12 L236 30 L250 50 L270 46 L290 52 L320 44"
    )
    onizleme = (
        '<div aria-hidden="true"><div class="lp-card">'
        '<div class="lp-top"><div><div class="lp-k">Anlık tüketim · Pres hattı</div>'
        '<div class="lp-v">1.284<small>kWh</small></div></div>'
        '<span class="lp-pill"><i></i>Anomali tespit edildi</span></div>'
        # Eğri 320x96'lık alanda çizilir, dikeyde 1.6 kat büyütülür; çizgi kalınlığı ve nokta bozulmasın diye
        # ölçek yalnız gruba uygulanır, çizgiler non-scaling-stroke ile ekrandaki kalınlığını korur.
        '<svg class="spark" viewBox="0 0 320 154">'
        '<defs><linearGradient id="lpf" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#60A5FA" stop-opacity=".35"/>'
        '<stop offset="1" stop-color="#60A5FA" stop-opacity="0"/></linearGradient></defs>'
        '<rect x="196" y="0" width="44" height="154" fill="#F87171" opacity=".12"/>'
        '<g transform="scale(1 1.6)">'
        '<path d="M0 72 L40 70 L80 64 L120 58 L160 56 L200 50 L240 50 L280 50 L320 50" fill="none" '
        'stroke="#94A3B8" stroke-width="2" stroke-dasharray="5 5" vector-effect="non-scaling-stroke"/>'
        f'<path d="{egri} L320 96 L0 96 Z" fill="url(#lpf)"/>'
        f'<path d="{egri}" fill="none" stroke="#60A5FA" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" '
        'vector-effect="non-scaling-stroke"/></g>'
        '<circle cx="222" cy="19.2" r="5.5" fill="#F87171" stroke="#0B1222" stroke-width="2.5"/></svg>'
        '<div class="lp-leg"><span><i class="a"></i>Gerçekleşen</span><span><i class="b"></i>Beklenen</span></div>'
        "</div></div>"
    )
    durum_html = (
        '<span class="lp-status"><i></i>Tüm sistemler çalışıyor</span>'
        if api_acik
        else '<span class="lp-status off"><i></i>Sunucuya şu an ulaşılamıyor</span>'
    )

    def _plan_html(pl):
        tik = svg("check", 16, "#4ADE80")
        return (
            f'<div class="lp-plan-ust"><h3>{pl["ad"]}</h3>'
            + ('<span class="lp-rozet">Önerilen</span>' if pl["kod"] == "pro" else "")
            + f'</div><div class="lp-kim">{pl["kim"]}</div>{pl["ucret"]}<div class="lp-kapsam">{pl["kapsam"]}</div>'
            + '<ul class="lp-maddeler">' + "".join(f"<li>{tik}<span>{m}</span></li>" for m in pl["maddeler"]) + "</ul>"
        )

    @st.dialog("Paket talebi", width="medium")
    def talep_formu(pl):
        teklif = pl["kod"] == "kurumsal"
        st.markdown(
            f'<div class="lg-title">{pl["ad"]} · {"Teklif iste" if teklif else "Satın al"}</div>'
            '<div class="lg-sub">Bilgilerinizi bırakın, satış ekibimiz bir iş günü içinde sizinle iletişime geçsin.</div>',
            unsafe_allow_html=True,
        )
        with st.form("talep"):
            c1, c2 = st.columns(2)
            ad = c1.text_input("Ad soyad *", autocomplete="name")
            sirket = c2.text_input("Şirket *", autocomplete="organization")
            eposta = c1.text_input("E-posta *", placeholder="ornek@sirket.com", autocomplete="email")
            tel = c2.text_input("Telefon", placeholder="05xx xxx xx xx", autocomplete="tel")
            sayac = c1.number_input("Sayaç sayısı", min_value=1, max_value=10_000, value=pl["sayac"], step=1)
            odeme = c2.radio("Ödeme modeli", ["Yıllık lisans", "Tasarruftan pay"], horizontal=True)
            notu = st.text_area("Mesajınız", placeholder="Tesisleriniz, ihtiyaçlarınız…", height=90)
            gonder = st.form_submit_button(
                "Teklif iste" if teklif else "Satın alma talebi gönder", type="primary", use_container_width=True
            )
        if gonder:
            if not (ad.strip() and sirket.strip() and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", eposta.strip())):
                st.error("Ad soyad, şirket ve geçerli bir e-posta adresi gerekli.")
                return
            # Talepler panel klasöründe CSV'ye yazılır (git'e girmez); satış ekibi buradan izler.
            dosya = Path(__file__).parent / "talepler.csv"
            yeni = not dosya.exists()
            with dosya.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if yeni:
                    w.writerow(["zaman", "paket", "ad_soyad", "sirket", "eposta", "telefon", "sayac", "odeme", "mesaj"])
                w.writerow([
                    datetime.now().isoformat(timespec="seconds"), pl["ad"], ad.strip(), sirket.strip(),
                    eposta.strip(), tel.strip(), sayac, odeme, notu.strip(),
                ])
            st.success("Talebiniz alındı. Satış ekibimiz en kısa sürede sizinle iletişime geçecek.")

    PLANLAR = [
        {
            "kod": "baslangic", "ad": "Başlangıç", "kim": "Küçük işletme, tek bina", "sayac": 3,
            "ucret": '<div class="lp-ucret">$120<small>/ sayaç / yıl</small></div>',
            "kapsam": "1–9 sayaç · kurulum $300 · ör. 3 sayaç $360 / yıl",
            "maddeler": ["Saatlik anomali tespiti ve TL maliyeti", "Her alarm için uygulanabilir öneri",
                         "Çalışma saatleri ve planlı duruş takvimi", "Aylık PDF tasarruf raporu", "2 kullanıcı"],
        },
        {
            "kod": "pro", "ad": "Profesyonel", "kim": "Otel, okul, kampüs", "sayac": 20,
            "ucret": '<div class="lp-ucret">$90<small>/ sayaç / yıl</small></div>',
            "kapsam": "10–39 sayaç · kurulum $1.000 · ör. 20 sayaç $2.070 / yıl",
            "maddeler": ["Başlangıç planındaki her şey", "Canlı izleme: saatlik veri, anlık alarm",
                         "CSV, Excel ve PDF raporlar", "Alarm durum takibi ve geri bildirim",
                         "10 kullanıcı, operatör ve yönetici rolleri"],
        },
        {
            "kod": "kurumsal", "ad": "Kurumsal", "kim": "AVM, fabrika, çok tesisli zincir", "sayac": 60,
            "ucret": '<div class="lp-ucret">$60<small>/ sayaç / yıl</small></div>',
            "kapsam": "40+ sayaç · kurulum $1.000 · ör. 60 sayaç $5.040 / yıl",
            "maddeler": ["Profesyonel planındaki her şey", "Tüm tesisler tek panelde",
                         "API erişimi ve mevcut sistemlerle entegrasyon", "Kurum içi sunucuya kurulum seçeneği",
                         "Öncelikli destek ve kurulum eğitimi"],
        },
    ]

    giris_ok = False
    ekran = st.container(key="lp_ekran")
    with ekran.container(horizontal=True, vertical_alignment="center", wrap=False):
        st.markdown(
            f'<div class="lp-brand">{LOGO}<div><b>{BRAND}</b><span>{BRAND_SUB}</span></div></div>',
            unsafe_allow_html=True, width="stretch",
        )
        st.markdown(durum_html, unsafe_allow_html=True, width="content")
        with st.popover("Giriş yap", icon=":material/login:", key="lp_giris"):
            st.markdown(
                '<div class="lg-title">Hesabınıza giriş yapın</div>'
                '<div class="lg-sub">Kurumsal e-posta adresinizi ve şifrenizi girin.</div>',
                unsafe_allow_html=True,
            )
            with st.form("giris"):
                email = st.text_input(
                    "E-posta", placeholder="ornek@sirket.com", icon=":material/mail:", autocomplete="email"
                )
                sifre = st.text_input(
                    "Şifre", type="password", placeholder="Şifrenizi girin", icon=":material/lock:",
                    autocomplete="current-password",
                )
                giris_btn = st.form_submit_button("Giriş yap", icon=":material/arrow_forward:", use_container_width=True)
            if giris_btn:
                if not email or not sifre:
                    st.warning("Lütfen e-posta ve şifrenizi girin.")
                else:
                    try:
                        with st.spinner("Giriş yapılıyor..."):
                            st.session_state.me = api.login(email.strip(), sifre)
                        giris_ok = True
                    except ApiError as e:
                        if e.status in (400, 401, 403, 422):
                            st.error("E-posta veya şifre hatalı. Lütfen tekrar deneyin.")
                        elif e.status == 429:
                            st.error("Çok fazla deneme yapıldı. Lütfen biraz bekleyip tekrar deneyin.")
                        elif e.status == 0:
                            st.error("Sunucuya ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.")
                        else:
                            st.error(f"Giriş şu an yapılamıyor ({e.status}). Lütfen daha sonra tekrar deneyin.")
            st.markdown(
                f'<div class="lg-secure"><i>{svg("lock", 16)}</i>'
                "<span>Oturum bilgisi tarayıcınızda değil, sunucuda tutulur.</span></div>"
                '<div class="lg-foot">Giriş yapamıyorsanız sistem yöneticinize başvurun.</div>',
                unsafe_allow_html=True,
            )

    sol, sag = ekran.container(key="lp_hero").columns([1.15, 1], gap="large", vertical_alignment="center")
    sol.markdown(
        '<div class="lp-hero">'
        f'<div class="lp-eyebrow">{svg("zap", 15, "#93C5FD")}Yapay zekâ destekli enerji izleme</div>'
        "<h1>İsrafı yakalayın, <em>tasarrufa dönüştürün.</em></h1>"
        f"<p>{BRAND}; AVM, fabrika, otel ve ofislerin elektrik sayaçlarını izler. Her saatin beklenen tüketimini "
        "modelle tahmin eder, israfı ve arızayı erken yakalar; bulduğu her anomali için maliyetini ve ne "
        "yapılması gerektiğini söyler.</p>"
        '<ul class="lp-flow" aria-label="Nasıl çalışır">'
        '<li>Sayaç verisi</li>'
        '<li>Anomali modeli</li>'
        '<li>TL maliyeti</li>'
        '<li class="ok">Öneri ve takip</li></ul></div>',
        unsafe_allow_html=True,
    )
    sag.markdown(onizleme, unsafe_allow_html=True)

    ekran.markdown(
        '<div class="lp-feats">'
        + _ozellik("chart", "Beklenen ve gerçekleşen", "Hava, takvim ve çalışma saatlerine göre her saatin beklenen tüketimi.")
        + _ozellik("alert", "Maliyetli anomali uyarıları", "Gece israfı, ani sıçrama, takılı ya da susan sayaç; her biri TL etkisiyle.")
        + _ozellik("bulb", "Uygulanabilir öneriler", "Her alarm için ne yapılacağı; iletildi, uygulandı, planlı olarak takip edin.")
        + _ozellik("radio", "Canlı izleme ve rapor", "MQTT ile anlık veri; CSV, Excel ve PDF tasarruf raporları.")
        + "</div>",
        unsafe_allow_html=True,
    )

    # Fiyatlar sunumdaki gelir modeliyle aynı: sayaç başına yıllık lisans, basamaklı (ilk 9 sayaç $120,
    # sonraki 30 sayaç $90, sonrası $60). Ön çalışmadır, pilotla kesinleşir.
    st.markdown(
        '<section class="lp-fiyat" aria-labelledby="lp-fiyat-baslik"><div class="lp-fiyat-bas">'
        f'<div class="lp-eyebrow">{svg("coins", 15, "#93C5FD")}Fiyatlandırma</div>'
        '<h2 id="lp-fiyat-baslik">İşletmenize uygun planı seçin</h2>'
        "<p>Sayaç başına yıllık lisans; sayaç arttıkça sayaç başı fiyat düşer. Her planda anomali tespiti, "
        "TL maliyet hesabı ve her alarm için öneri bulunur.</p></div></section>",
        unsafe_allow_html=True,
    )
    for sutun, pl in zip(st.columns(3), PLANLAR, strict=True):
        with sutun.container(key=f"lp_plan_{pl['kod']}"):
            st.markdown(_plan_html(pl), unsafe_allow_html=True)
            etiket = "Teklif iste" if pl["kod"] == "kurumsal" else "Satın al"
            if st.button(etiket, key=f"lp_al_{pl['kod']}", icon=":material/arrow_forward:", use_container_width=True):
                talep_formu(pl)
    st.markdown(
        '<div class="lp-notlar">'
        f'<span>{svg("zap", 14, "#93C5FD")}Basamaklı: ilk 9 sayaç $120, sonraki 30 sayaç $90, sonrası $60</span>'
        f'<span>{svg("leaf", 14, "#93C5FD")}İsterseniz lisans yerine doğrulanmış tasarrufun %20’si</span>'
        f'<span>{svg("calendar", 14, "#93C5FD")}Pilot: ilk 3 ay indirimli</span>'
        f'<span>{svg("file", 14, "#93C5FD")}Fiyatlara KDV dahil değildir</span>'
        "</div>"
        f'<div class="lp-foot"><span>© {datetime.now().year} {BRAND} · {BRAND_SUB}</span>'
        "<span>AVM · Fabrika · Otel · Ofis · Eğitim</span></div>",
        unsafe_allow_html=True,
    )
    if giris_ok:
        st.rerun()
    st.stop()

me = st.session_state.me
who = me.get("name") or me["email"]

# ---------- Temel veri ----------
try:
    buildings = api.buildings()
    # İşletmeye bağlı sayaç varsa zil ve rozet de yalnız onları sayar (aşağıdaki "musteri" ile aynı).
    summary = api.summary(customers_only=any(b.get("tenant_id") is not None for b in buildings))
except ApiError as e:
    if e.status == 401:
        st.session_state.clear()
        st.rerun()
    st.error(f"Veri alınamadı: {e}")
    st.stop()

if not buildings:
    st.warning("Henüz sayaç verisi yok.")
    st.stop()

# CRM yalnız işletmeye bağlı sayaçları gösterir; geri kalan LEAD sayaçları model eğitimi içindir.
# Hiç işletme yoksa (demo modu) hepsi görünür.
musteri = [b for b in buildings if b.get("tenant_id") is not None]
if musteri:
    buildings = musteri

names = {b["building_id"]: (b["name"] or b["external_id"]) for b in buildings}
# Platform yöneticisi birden çok işletme görür: sayacın başına işletme adını ekle.
if me.get("tenant_id") is None:
    names = {
        b["building_id"]: f"{b['tenant_name']} · {names[b['building_id']]}"
        if b.get("tenant_name") else names[b["building_id"]]
        for b in buildings
    }
# Her sayacın kendi tarifesi; eski API alanı vermezse varsayılan.
tarife = {b["building_id"]: b.get("tariff_tl_per_kwh") or TARIFF_TL for b in buildings}
ids = sorted(names, key=lambda i: names[i].casefold())
en_cok = max(buildings, key=lambda b: b["anomaly_count"])["building_id"]

# Canlı İzleme yalnız platform yöneticisine ve canlı demo tesisinin kendi kullanıcısına görünür;
# başka bir işletmenin kullanıcısı orada göremeyeceği bir tesisin boş sayfasıyla karşılaşmasın.
try:
    canli_tid = api.live_demo().get("tenant_id")
except ApiError:
    canli_tid = None
canli_gorur = me.get("tenant_id") is None or me.get("tenant_id") == canli_tid

# ---------- Sol menü ----------
PAGES = [
    ("genel", ":material/home:", "Genel Bakış"),
    ("isletme", ":material/storefront:", "İşletmeler" if me.get("tenant_id") is None else "İşletmem"),
    ("canli", ":material/sensors:", "Canlı İzleme"),
    ("tuketim", ":material/bar_chart:", "Tüketim Analizi"),
    ("anomali", ":material/warning:", "Anomaliler"),
    ("rapor", ":material/description:", "Raporlar"),
    ("ayar", ":material/settings:", "Ayarlar"),
]
if not canli_gorur:
    PAGES = [p for p in PAGES if p[0] != "canli"]
if st.session_state.get("sayfa") not in {p[0] for p in PAGES}:
    st.session_state.sayfa = "genel"  # girişten sonra ilk açılan sayfa
if st.session_state.get("bina") not in ids:
    st.session_state.bina = en_cok


def set_page(p):
    st.session_state.sayfa = p


with st.sidebar:
    st.markdown(
        f'<div class="brand">{LOGO}<div><div class="brand-name">{BRAND}</div>'
        f'<div class="brand-sub">{BRAND_SUB}</div></div></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="nav"):
        for pid, icon, ad in PAGES:
            st.button(
                ad,
                key=f"nav_{pid}",
                icon=icon,
                use_container_width=True,
                type="primary" if st.session_state.sayfa == pid else "secondary",
                on_click=set_page,
                args=(pid,),
            )
    st.markdown(f"<style>{sidebar_css(summary['active_alerts'])}</style>", unsafe_allow_html=True)
    with st.container(key="bina_box"):
        bid = st.selectbox("Sayaç", ids, format_func=lambda i: names[i], key="bina")
        if st.button("Çıkış"):
            try:
                api.logout()
            finally:
                st.session_state.clear()
                st.rerun()

# ---------- Ortak: öneri kartı ve durum düğmeleri ----------
yazabilir = me["role"] in ("admin", "operator")


def gonder(aid, verdict, not_key=None):
    note = (st.session_state.get(not_key) or "").strip() if not_key else ""
    try:
        api.feedback(aid, verdict, note or None)
        if not_key:
            st.session_state[not_key] = ""
        st.session_state.fb_msg = ("ok", f"Durum kaydedildi: {VER[verdict][0]}.")
    except ApiError as e:
        st.session_state.fb_msg = ("err", f"Kaydedilemedi: {e}")


def mesaj_goster():
    msg = st.session_state.pop("fb_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])


def durum_dugmeleri(a, prefix, not_key=None):
    """Öneri durumu düğmeleri (yalnız operatör ve admin); çağıran yatay bir kapsayıcı açar.

    Anahtarlar style.css'teki renklerle eşleşir: detayda d_<kod>_<id>, listelerde <kod>_<önek><id>."""
    if not yazabilir:
        return
    for kod, etiket, ikon in KARAR:
        key = f"d_{kod}_{a['id']}" if prefix == "d" else f"{kod}_{prefix}{a['id']}"
        st.button(etiket, key=key, icon=ikon, on_click=gonder, args=(a["id"], kod, not_key),
                  type="primary" if a["verdict"] == kod else "secondary")


def ac_detay(aid, building_id):
    st.session_state.detay = (aid, building_id)
    st.session_state.sayfa = "anomali"


def kapat_detay():
    st.session_state.pop("detay", None)


def ac_sayac(building_id):
    st.session_state.bina = building_id
    st.session_state.sayfa = "genel"


def ac_isletme(tid):
    st.session_state.isletme_id = tid


def sayac_adi(a):
    return names.get(a["building_id"]) or a.get("building_name") or str(a["building_id"])


# ---------- İşletmeler sayfası ----------
if st.session_state.sayfa == "isletme":
    try:
        gun = DONEMLER[st.session_state.get("isl_donem") or "90 gün"]
        ozetler = api.tenant_summaries(gun)
    except ApiError as e:
        st.error(f"İşletme özeti alınamadı: {e}")
        st.stop()
    if not ozetler:
        st.info("Henüz işletme tanımlı değil. Sayaçları işletmelere bağlamak için "
                "`python -m src.data.catalog` çalıştırın.")
        st.stop()
    mesaj_goster()
    st.segmented_control("Dönem", list(DONEMLER), default="90 gün", key="isl_donem")

    tek = len(ozetler) == 1
    tid = ozetler[0]["tenant_id"] if tek else st.session_state.get("isletme_id")
    o = next((x for x in ozetler if x["tenant_id"] == tid), None)

    if o is None:
        st.markdown(
            '<div class="hello">İşletmeler</div>'
            f'<div class="hello-sub">{len(ozetler)} işletme · her sayacın son {gun} günü</div>',
            unsafe_allow_html=True,
        )
        st.write("")
        k1, k2, k3, k4 = st.columns(4)
        k1.markdown(kpi_card(svg("coins", 24), "orange", "Toplam İsraf", tl(sum(x["waste_tl"] for x in ozetler)),
                             f"<span>(son {gun} gün)</span>"), unsafe_allow_html=True)
        k2.markdown(kpi_card(svg("alert", 24), "red", "Açık Alarm", str(sum(x["open_alerts"] for x in ozetler)),
                             "<span>(işlem bekleyen israf)</span>"), unsafe_allow_html=True)
        k3.markdown(kpi_card(svg("bell", 24), "blue", "Veri Sorunu", str(sum(x["data_issues"] for x in ozetler)),
                             "<span>(takılı ya da susan sayaç)</span>"), unsafe_allow_html=True)
        k4.markdown(kpi_card(svg("zap", 24), "green", "Sayaç", str(sum(len(x["meters"]) for x in ozetler)),
                             f"<span>({len(ozetler)} işletmede)</span>"), unsafe_allow_html=True)
        st.write("")
        cols = st.columns(3)
        for k, x in enumerate(sorted(ozetler, key=lambda x: -x["waste_tl"])):
            pahali = x["meters"][0] if x["meters"] and x["meters"][0]["waste_tl"] > 0 else None
            alt = " · ".join(filter(None, [x["city"], f"{len(x['meters'])} sayaç"]))
            with cols[k % 3], st.container(key=f"card_isl_{x['tenant_id']}"):
                st.markdown(
                    f'<div class="tn-head"><span class="tn-name">{html.escape(x["name"])}</span>'
                    + ('<span class="badge sev-low">● Canlı</span>' if x["tenant_id"] == canli_tid
                       else f'<span class="badge sev-blue">{SEGMENT_TR.get(x["segment"], "İşletme")}</span>')
                    + '</div>'
                    f'<div class="tn-sub">{html.escape(alt)}</div>'
                    '<div class="tn-stats">'
                    f'<div><b>{tl(x["waste_tl"])}</b><span>İsraf ({gun} gün)</span></div>'
                    f'<div><b>{x["open_alerts"]}</b><span>Açık alarm</span></div>'
                    f'<div><b>{x["data_issues"]}</b><span>Veri sorunu</span></div></div>'
                    + (f'<div class="tn-top">En pahalı sayaç: <b>{html.escape(pahali["name"] or "-")}</b> '
                       f'({tl(pahali["waste_tl"])})</div>' if pahali else '<div class="tn-top">İsraf yok</div>'),
                    unsafe_allow_html=True,
                )
                st.button("İşletmeyi aç", key=f"isl_ac_{x['tenant_id']}", icon=":material/arrow_forward:",
                          on_click=ac_isletme, args=(x["tenant_id"],), use_container_width=True)
        st.stop()

    # Tek işletme: özet, bugün yapılacaklar, konuma göre sayaçlar
    if not tek:
        st.button("Tüm işletmeler", icon=":material/arrow_back:", on_click=ac_isletme, args=(None,),
                  key="isl_geri")
    son_gun = tr_tarih(pd.Timestamp(o["period_end"])) if o["period_end"] else "-"
    st.markdown(
        f'<div class="hello">{html.escape(o["name"])}</div>'
        f'<div class="hello-sub">{SEGMENT_TR.get(o["segment"], "İşletme")} · {len(o["meters"])} sayaç'
        f' · son {gun} gün (veri sonu {son_gun})</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(kpi_card(svg("coins", 24), "orange", "İsraf Maliyeti", tl(o["waste_tl"]), f"<span>(son {gun} gün)</span>"),
                unsafe_allow_html=True)
    k2.markdown(kpi_card(svg("zap", 24), "blue", "Fazla Tüketim", f"{num(o['waste_kwh'])} kWh", "<span>(beklenenin üstü)</span>"),
                unsafe_allow_html=True)
    k3.markdown(kpi_card(svg("alert", 24), "red", "Açık Alarm", str(o["open_alerts"]), "<span>(işlem bekleyen israf)</span>"),
                unsafe_allow_html=True)
    k4.markdown(kpi_card(svg("bell", 24), "green", "Veri Sorunu", str(o["data_issues"]), "<span>(israf değil, sayaç kontrolü)</span>"),
                unsafe_allow_html=True)
    st.write("")

    since = (pd.Timestamp(o["period_end"]) - timedelta(days=gun)).isoformat() if o["period_end"] else None
    try:
        yapilacak = api.anomalies(None, limit=3, tenant_id=o["tenant_id"], category="waste",
                                  status="open", order="cost", since=since)
    except ApiError as e:
        st.error(f"Alarmlar alınamadı: {e}")
        st.stop()
    with st.container(key="card_bugun"):
        st.markdown('<div class="card-title">Bugün ne yapmalı?</div>', unsafe_allow_html=True)
        st.caption("Açık israf alarmlarından en pahalı üçü; her biri için ekipmana göre öneri.")
        if not yapilacak:
            st.success("Açık israf alarmı yok.")
        for a in yapilacak:
            with st.container(key=f"anom_todo_{a['id']}"):
                st.markdown(
                    f'<div class="an-row-top"><span class="badge sev-mid">{tl(a["est_cost_tl"])}</span>'
                    f'{verdict_badge(a)}<b class="todo-meter">{html.escape(sayac_adi(a))}</b>'
                    f'<span class="an-time">{tr_tarih_saat(pd.Timestamp(a["ts_start"]))}</span></div>'
                    + rec_html(a)
                    + f'<div class="an-desc">{html.escape(tarihsiz(a["explanation"]))}</div>',
                    unsafe_allow_html=True,
                )
                with st.container(horizontal=True, gap="small", key=f"anbtn_t{a['id']}"):
                    st.button("Detay", key=f"dt_t{a['id']}", icon=":material/visibility:", on_click=ac_detay,
                              args=(a["id"], a["building_id"]))
                    st.button("Sayaç", key=f"t_sy_{a['id']}", icon=":material/show_chart:", on_click=ac_sayac,
                              args=(a["building_id"],))
                    durum_dugmeleri(a, "t")
    st.write("")

    with st.container(key="card_sayaclar"):
        st.markdown('<div class="card-title">Sayaçlar (konuma göre)</div>', unsafe_allow_html=True)
        h = st.columns([2.2, 2.6, 1.4, 1.3, 1, 1, 1.1])
        for col, baslik in zip(h, ["Konum", "Makine / bölge", "Ekipman", f"İsraf ({gun} gün)",
                                   "Açık alarm", "Veri sorunu", ""], strict=True):
            col.markdown(f'<div class="mt-h">{baslik}</div>', unsafe_allow_html=True)
        for m in sorted(o["meters"], key=lambda m: (m["name"] or "").casefold()):
            r = st.columns([2.2, 2.6, 1.4, 1.3, 1, 1, 1.1], vertical_alignment="center")
            r[0].markdown(f'<div class="mt-n">{html.escape(m["name"] or "-")}</div>', unsafe_allow_html=True)
            r[1].markdown(f'<div class="mt-c">{html.escape(m["asset_name"] or "-")}</div>', unsafe_allow_html=True)
            r[2].markdown(f'<div class="mt-c">{EKIPMAN_TR.get(m["equipment_type"], "-")}</div>', unsafe_allow_html=True)
            r[3].markdown(f'<div class="mt-v">{tl(m["waste_tl"])}</div>', unsafe_allow_html=True)
            r[4].markdown(f'<div class="mt-v">{m["open_alerts"]}</div>', unsafe_allow_html=True)
            r[5].markdown(f'<div class="mt-v">{m["data_issues"]}</div>', unsafe_allow_html=True)
            r[6].button("Aç", key=f"sy_ac_{m['building_id']}", icon=":material/arrow_forward:",
                        on_click=ac_sayac, args=(m["building_id"],), use_container_width=True)
    st.stop()

# ---------- Canlı İzleme sayfası ----------
if st.session_state.sayfa == "canli":
    st.markdown(
        '<div class="hello">Canlı İzleme</div>'
        '<div class="hello-sub">Canlı Demo Tesisi · sanal sayaçlar MQTT ile veri gönderir, '
        "model yeni saatleri birkaç saniyede bir kontrol eder</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    def canli_islem(baslat):
        try:
            (api.live_start if baslat else api.live_stop)()
            st.session_state.fb_msg = (
                ("ok", "Canlı demo başladı; ilk alarmlar 1-2 dakika içinde düşer.")
                if baslat else ("ok", "Canlı demo durduruldu.")
            )
        except ApiError as e:
            st.session_state.fb_msg = ("err", f"İşlem yapılamadı: {e}")

    @st.fragment(run_every=10)
    def canli_ekran():
        mesaj_goster()
        try:
            d = api.live_demo()
        except ApiError as e:
            st.error(f"Canlı demo durumu alınamadı: {e}")
            return
        sim = pd.Timestamp(d["sim_ts"]) if d["sim_ts"] else None
        platform_admin = me["role"] == "admin" and me.get("tenant_id") is None

        if not d["available"]:
            durum = ('<span class="lv-dot off"></span>Canlı demo servisi çalışmıyor. '
                     "Proje klasöründe <code>docker compose up -d</code> çalıştırın.")
        elif d["running"] and sim is None:
            durum = '<span class="lv-dot on"></span>Başlatılıyor: son 4 haftanın verisi gönderiliyor…'
        elif d["running"]:
            durum = (f'<span class="lv-dot on"></span>Canlı veri akıyor · sanal saat '
                     f"<b>{tr_tarih_saat(sim)}</b> · her dakika 1 saat ilerler")
        else:
            durum = '<span class="lv-dot idle"></span>Canlı demo hazır, başlatılmayı bekliyor.'
        s1, s2 = st.columns([4, 1.6], vertical_alignment="center")
        s1.markdown(f'<div class="lv-status">{durum}</div>', unsafe_allow_html=True)
        with s2, st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if platform_admin and d["available"]:
                st.button("Baştan başlat" if d["running"] else "Canlı demoyu başlat",
                          icon=":material/play_circle:", type="primary", key="lv_start",
                          on_click=canli_islem, args=(True,))
                if d["running"]:
                    st.button("Durdur", icon=":material/stop_circle:", key="lv_stop",
                              on_click=canli_islem, args=(False,))

        if not d["meters"]:
            st.info("Canlı demo tesisinin sayaçları henüz yok ya da bu hesap onları göremiyor.")
            return
        try:
            alarmlar = api.anomalies(None, limit=100, fresh=True, tenant_id=d["tenant_id"])
        except ApiError as e:
            st.error(f"Alarmlar alınamadı: {e}")
            return

        # Sayaç kartları: son okuma ve son 3 saatte süren alarm
        aktif = {}
        for a in alarmlar:
            if sim is not None and pd.Timestamp(a["ts_end"]) >= sim - timedelta(hours=3):
                aktif.setdefault(a["building_id"], a)
        st.write("")
        for col, m in zip(st.columns(len(d["meters"])), d["meters"], strict=True):
            a = aktif.get(m["building_id"])
            if a is None:
                rozet = '<span class="badge sev-low">Normal</span>'
            elif a["category"] == "data_issue":
                rozet = '<span class="badge sev-data">Veri sorunu</span>'
            else:
                rozet = '<span class="badge sev-high">Alarm</span>'
            son = (f'{num(m["last_kwh"], 1)} kWh' if m["last_kwh"] is not None else "veri yok") if m["last_ts"] else "-"
            zaman = tr_tarih_saat(pd.Timestamp(m["last_ts"])) if m["last_ts"] else "henüz veri yok"
            col.markdown(
                f'<div class="lv-card"><div class="lv-top"><span class="lv-name">{html.escape(m["name"] or "-")}</span>{rozet}</div>'
                f'<div class="lv-eq">{EKIPMAN_TR.get(m["equipment_type"], "-")}</div>'
                f'<div class="lv-kwh">{son}</div><div class="lv-ts">{zaman}</div></div>',
                unsafe_allow_html=True,
            )

        st.write("")
        sol, sag = st.columns([1.6, 1])
        with sol, st.container(key="card_lv_grafik"):
            secenek = {m["building_id"]: m["name"] or str(m["building_id"]) for m in d["meters"]}
            if st.session_state.get("lv_sayac") not in secenek:
                st.session_state.lv_sayac = next(iter(secenek))
            t1, t2 = st.columns([1.4, 2], vertical_alignment="center")
            t1.markdown('<div class="card-title">Son 48 saat</div>', unsafe_allow_html=True)
            secili = t2.selectbox("Sayaç", list(secenek), format_func=secenek.get, key="lv_sayac",
                                  label_visibility="collapsed")
            try:
                ham = pd.DataFrame(api.readings(secili, 48, fresh=True))
                tah = pd.DataFrame(api.forecast(secili, 48, fresh=True))
            except ApiError:
                ham = tah = pd.DataFrame()
            if ham.empty:
                st.info("Bu sayaçta henüz veri yok; canlı demo başlatılınca dolar.")
            else:
                ham["ts"] = pd.to_datetime(ham["ts"], utc=True)
                if not tah.empty:
                    tah["ts"] = pd.to_datetime(tah["ts"], utc=True)
                ev = build_events(ham, tah, [a for a in alarmlar if a["building_id"] == secili])
                fig = consumption_chart(ham, tah, ev, "Gün", figsize=(10, 4))
                st.markdown(fig_html(fig, "chart", chart_alt(ham, tah, ev)), unsafe_allow_html=True)

        with sag, st.container(key="card_lv_alarm"):
            st.markdown('<div class="card-title">Son düşen alarmlar</div>', unsafe_allow_html=True)
            if not alarmlar:
                st.caption("Henüz alarm yok. Başlattıktan sonra ilk alarmlar 1-2 dakika içinde düşer.")
            for a in alarmlar[:6]:
                ad, ton = KAT.get(a["category"], ("Diğer", "data"))
                with st.container(key=f"anom_lv{a['id']}"):
                    st.markdown(
                        f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{verdict_badge(a)}'
                        f'<b class="todo-meter">{html.escape(a.get("building_name") or "-")}</b>'
                        f'<span class="an-time">{tr_tarih_saat(pd.Timestamp(a["ts_start"]))}</span></div>'
                        f'<div class="an-desc">{html.escape(kisa_aciklama(a["explanation"] or ""))}</div>'
                        + rec_html(a),
                        unsafe_allow_html=True,
                    )
                    with st.container(horizontal=True, gap="small", key=f"anbtn_lv{a['id']}"):
                        durum_dugmeleri(a, "lv")

    canli_ekran()
    st.stop()

# ---------- Anomaliler sayfası ----------
if st.session_state.sayfa == "anomali":
    mesaj_goster()

    if st.session_state.get("detay") is not None:
        aid, abid = st.session_state.detay
        try:
            a = next((x for x in api.anomalies(abid, limit=500) if x["id"] == aid), None)
        except ApiError as e:
            st.error(f"Anomali alınamadı: {e}")
            st.stop()
        if a is None:
            kapat_detay()
            st.rerun()
        s = pd.to_datetime(a["ts_start"], utc=True)
        e = pd.to_datetime(a["ts_end"], utc=True)
        ad, ton = KAT.get(a["category"], ("Diğer", "data"))

        st.button("Listeye dön", icon=":material/arrow_back:", on_click=kapat_detay, key="detay_geri")
        st.markdown(
            '<div class="hello">Anomali Detayı</div>'
            f'<div class="hello-sub">{html.escape(sayac_adi(a))} · {tr_tarih_saat(s)} → {tr_tarih_saat(e)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{verdict_badge(a)}</div>',
                    unsafe_allow_html=True)

        try:
            pay = timedelta(hours=36)
            son_saat = pd.Timestamp(a["ts_end"]) + pay
            saat = int((son_saat - pd.Timestamp(a["ts_start"]) + pay) / timedelta(hours=1)) + 1
            ham = pd.DataFrame(api.readings(abid, min(saat, 2160), son_saat.isoformat()))
            tah = pd.DataFrame(api.forecast(abid, min(saat, 2160), son_saat.isoformat()))
        except ApiError as err:
            st.error(f"Veri alınamadı: {err}")
            st.stop()

        gercek = beklenen = None
        pencere = tah_p = pd.DataFrame()
        if not ham.empty:
            ham["ts"] = pd.to_datetime(ham["ts"], utc=True)
            gercek = float(ham[(ham["ts"] >= s) & (ham["ts"] <= e)]["kwh"].sum())
            pencere = ham[(ham["ts"] >= s - timedelta(hours=36)) & (ham["ts"] <= e + timedelta(hours=36))]
        if not tah.empty:
            tah["ts"] = pd.to_datetime(tah["ts"], utc=True)
            beklenen = float(tah[(tah["ts"] >= s) & (tah["ts"] <= e)].groupby("ts")["expected_kwh"].mean().sum())
            tah_p = tah[(tah["ts"] >= s - timedelta(hours=36)) & (tah["ts"] <= e + timedelta(hours=36))]

        sure = (e - s).total_seconds() / 3600 + 1
        fark = "-"
        if gercek is not None and beklenen:
            oran = (gercek / beklenen - 1) * 100
            fark = f"%{abs(oran):.0f} {'fazla' if oran >= 0 else 'az'}"
        d1, d2, d3, d4 = st.columns(4)
        d1.markdown(kpi_card(svg("calendar", 24), "blue", "Süre", f"{sure:.0f} saat", "<span>(anomali penceresi)</span>"), unsafe_allow_html=True)
        d2.markdown(kpi_card(svg("zap", 24), "red", "Gerçekleşen", f"{num(gercek)} kWh" if gercek is not None else "-", f"<span>({fark})</span>"), unsafe_allow_html=True)
        d3.markdown(kpi_card(svg("chart", 24), "green", "Beklenen (baseline)", f"{num(beklenen)} kWh" if beklenen is not None else "-", "<span>(model tahmini)</span>"), unsafe_allow_html=True)
        d4.markdown(kpi_card(svg("coins", 24), "orange", "Fazla Tüketim", f"{num(a['excess_kwh'], 1)} kWh" if a["excess_kwh"] is not None else "-", f"<span>({tl(a['est_cost_tl'])})</span>"), unsafe_allow_html=True)
        st.write("")

        with st.container(key="card_detay_grafik"):
            st.markdown('<div class="card-title">Beklenen ve gerçekleşen tüketim</div>', unsafe_allow_html=True)
            if pencere.empty:
                st.info("Bu anomali için ölçüm verisi bulunamadı.")
            else:
                ev = build_events(pencere, tah_p, [a])
                _, kapali_p, durus_p = takvim_bantlari(
                    api, abid, pencere["ts"].min(), pencere["ts"].max() + timedelta(hours=1)
                )
                lejant = bant_lejant(kapali_p, durus_p)
                if lejant:
                    st.markdown(f'<div class="legend">{lejant}</div>', unsafe_allow_html=True)
                fig = consumption_chart(pencere, tah_p, ev, "Hafta", kapali_p, durus_p, figsize=(12, 4.2))
                st.markdown(fig_html(fig, "chart", chart_alt(pencere, tah_p, ev, durus_p)), unsafe_allow_html=True)
                grafik_tablosu(pencere, tah_p)

        st.write("")
        with st.container(key="card_detay_bilgi"):
            st.markdown('<div class="card-title">Açıklama ve öneri</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="an-desc">{html.escape(tarihsiz(a["explanation"]) or "-")}</div>'
                + rec_html(a)
                + (f'<div class="an-advice">{svg("bulb", 17)}<span>{html.escape(a["advice"])}</span></div>' if a["advice"] else ""),
                unsafe_allow_html=True,
            )

        st.write("")
        with st.container(key="card_detay_geri"):
            st.markdown('<div class="card-title">Durum ve değerlendirme</div>', unsafe_allow_html=True)
            # API şimdilik notu döndürmüyor; alan eklenince burada görünür.
            if a.get("note"):
                st.markdown(
                    f'<div class="an-note">{svg("message", 16)}<span>{html.escape(a["note"])}</span></div>',
                    unsafe_allow_html=True,
                )
            if not yazabilir:
                st.caption("Durum vermek için operatör veya yönetici rolü gerekir.")
            else:
                v = VER.get(a["verdict"])
                if v:
                    st.caption(f"Mevcut durum: {v[0]}. Yeni bir durum seçerseniz son seçim geçerli olur.")
                nk = f"d_not_{a['id']}"
                st.text_area(
                    "Not (isteğe bağlı)", key=nk, max_chars=1000, height=90,
                    placeholder="Örn. kompresör arızası, bakım ekibine iletildi.",
                )
                with st.container(horizontal=True, gap="small"):
                    durum_dugmeleri(a, "d", nk)
        st.stop()

    tum_sayac = st.session_state.get("an_kapsam") == "Tüm sayaçlar"
    st.markdown(
        '<div class="hello">Anomaliler</div>'
        f'<div class="hello-sub">{"Tüm sayaçlar" if tum_sayac else html.escape(names[bid])} · israf ve veri sorunları</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    f1, f2, f3, f4 = st.columns(4)
    f1.selectbox("Kapsam", ["Seçili sayaç", "Tüm sayaçlar"], key="an_kapsam")
    durum_f = f2.selectbox("Durum", list(DURUM_FILTRE), key="an_durum")
    ekipman_f = f3.selectbox("Ekipman", ["Tümü", *EKIPMAN_TR], format_func=lambda k: EKIPMAN_TR.get(k, k), key="an_ekipman")
    sira_f = f4.selectbox("Sıralama", ["En yeni", "En pahalı"], key="an_sira")
    filtre = {
        "status": DURUM_FILTRE[durum_f],
        "equipment_type": None if ekipman_f == "Tümü" else ekipman_f,
        "order": "cost" if sira_f == "En pahalı" else "recent",
    }
    hedef = None if tum_sayac else bid
    try:
        israf = api.anomalies(hedef, limit=LISTE_LIMIT, category="waste", **filtre)
        veri_sor = api.anomalies(hedef, limit=LISTE_LIMIT, category="data_issue", **filtre)
    except ApiError as e:
        st.error(f"Anomaliler alınamadı: {e}")
        st.stop()

    acik = [a for a in israf if a["verdict"] not in KAPALI_DURUM]
    k1, k2, k3 = st.columns(3)
    k1.markdown(kpi_card(svg("alert", 24), "red", "Açık İsraf Alarmı", str(len(acik)), "<span>(listedekiler içinde)</span>"),
                unsafe_allow_html=True)
    k2.markdown(kpi_card(svg("coins", 24), "orange", "Açık Alarmların Maliyeti", tl(sum(a["est_cost_tl"] or 0 for a in acik)),
                         "<span>(tahmini)</span>"), unsafe_allow_html=True)
    k3.markdown(kpi_card(svg("bell", 24), "blue", "Veri Sorunu", str(len(veri_sor)), "<span>(israf değil, sayaç kontrolü)</span>"),
                unsafe_allow_html=True)
    st.write("")

    def liste(rows, prefix):
        if not rows:
            st.info("Filtreye uyan kayıt yok.")
            return
        st.caption(f"{len(rows)}{'+' if len(rows) == LISTE_LIMIT else ''} kayıt"
                   + (" (ilk 25 gösteriliyor)" if len(rows) > 25 else ""))
        for a in rows[:25]:
            ad, ton = KAT.get(a["category"], ("Diğer", "data"))
            bas = tr_tarih_saat(pd.to_datetime(a["ts_start"], utc=True))
            bit = tr_tarih_saat(pd.to_datetime(a["ts_end"], utc=True))
            fazla = f"{num(a['excess_kwh'], 1)} kWh" if a["excess_kwh"] is not None else "-"
            sayac = f'<b class="todo-meter">{html.escape(sayac_adi(a))}</b>' if tum_sayac else ""
            maliyet = (f'<div class="an-cost">Fazla tüketim: <b>{fazla}</b> · Tahmini maliyet etkisi: '
                       f'<b>{tl(a["est_cost_tl"])}</b></div>') if a["category"] == "waste" else ""
            with st.container(key=f"anom_{prefix}_{a['id']}"):
                st.markdown(
                    f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{verdict_badge(a)}{sayac}'
                    f'<span class="an-time">{bas} → {bit}</span></div>'
                    f'<div class="an-desc">{html.escape(tarihsiz(a["explanation"]) or "-")}</div>{maliyet}'
                    + rec_html(a)
                    + (f'<div class="an-advice">{svg("bulb", 17)}<span>{html.escape(a["advice"])}</span></div>'
                       if a["advice"] else ""),
                    unsafe_allow_html=True,
                )
                with st.container(horizontal=True, gap="small", key=f"anbtn_{prefix}{a['id']}"):
                    st.button("Detay", key=f"dt_{prefix}{a['id']}", icon=":material/visibility:", on_click=ac_detay,
                              args=(a["id"], a["building_id"]))
                    durum_dugmeleri(a, prefix)

    t1, t2 = st.tabs([f"İsraf ({len(israf)})", f"Veri sorunları ({len(veri_sor)})"])
    with t1:
        liste(israf, "w")
    with t2:
        liste(veri_sor, "v")
    st.stop()

# ---------- Ayarlar sayfası ----------
if st.session_state.sayfa == "ayar":
    st.markdown(
        '<span class="pg-okunur" hidden></span><div class="hello">Ayarlar</div>'
        f'<div class="hello-sub">Hesap bilgileri · {html.escape(names[bid])} için çalışma saatleri ve planlı duruşlar</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    with st.container(key="card_ayar"):
        st.markdown('<div class="card-title">Hesap</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="set-row"><span>Ad</span><span>{html.escape(who)}</span></div>'
            f'<div class="set-row"><span>E-posta</span><span>{html.escape(me["email"])}</span></div>'
            f'<div class="set-row"><span>Rol</span><span>{html.escape(me["role"])}</span></div>'
            f'<div class="set-row"><span>API adresi</span><span>{html.escape(API_URL)}</span></div>'
            f'<div class="set-row"><span>Tarife ({html.escape(names[bid])})</span>'
            f'<span>{num(tarife[bid], 2)} TL/kWh</span></div>',
            unsafe_allow_html=True,
        )

    yazabilir = me["role"] in ("admin", "operator")
    msg = st.session_state.pop("ayar_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])

    # --- Çalışma saatleri ---
    st.write("")
    with st.container(key="card_saatler"):
        st.markdown('<div class="card-title">Çalışma saatleri (vardiyalar)</div>', unsafe_allow_html=True)
        st.caption(
            "Bu saatlerin dışı tüketim grafiklerinde bantla gösterilir ve Genel Bakış'ta mesai dışı "
            "tüketim hesaplanır. Kapanış saati açılıştan önceyse (örn. 22:00 → 06:00) vardiya gece "
            "yarısını geçer."
        )
        try:
            kayitli = [
                (h["weekday"], dtime.fromisoformat(h["opens"]), dtime.fromisoformat(h["closes"]))
                for h in api.operating_hours(bid)
            ]
        except ApiError as e:
            st.error(f"Çalışma saatleri alınamadı: {e}")
            kayitli = None

        if kayitli is not None:
            ver = st.session_state.get("saat_ver", 0)
            taslak = st.session_state.get("saat_taslak")
            satirlar = taslak[1] if taslak and taslak[0] == bid else kayitli
            if taslak and taslak[0] == bid:
                st.info("Şablon uygulandı; kalıcı olması için Kaydet'e basın.")

            df = pd.DataFrame(
                [{"Gün": GUNLER[g - 1], "Açılış": a, "Kapanış": k} for g, a, k in satirlar],
                columns=["Gün", "Açılış", "Kapanış"],
            )
            duzen = st.data_editor(
                df,
                key=f"saat_{bid}_{ver}",
                num_rows="dynamic" if yazabilir else "fixed",
                disabled=not yazabilir,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Gün": st.column_config.SelectboxColumn(options=GUNLER, required=True),
                    "Açılış": st.column_config.TimeColumn(format="HH:mm", step=900, required=True),
                    "Kapanış": st.column_config.TimeColumn(format="HH:mm", step=900, required=True),
                },
            )
            if duzen.empty:
                st.caption("Tanımlı çalışma saati yok.")

            if yazabilir:
                c1, c2, c3, _ = st.columns([2.2, 1, 1, 1.5])
                sablon = c1.selectbox("Şablon", list(SABLON), label_visibility="collapsed", key="saat_sablon")
                if c2.button("Şablonu uygula", key="saat_uygula"):
                    st.session_state.saat_taslak = (bid, SABLON[sablon])
                    st.session_state.saat_ver = ver + 1
                    st.rerun()
                if c3.button("Kaydet", key="saat_kaydet", type="primary", icon=":material/save:"):
                    hata, periyot = saatleri_dogrula(duzen)
                    if hata:
                        st.error(hata)
                    else:
                        try:
                            api.set_operating_hours(bid, periyot)
                            st.session_state.ayar_msg = ("ok", "Çalışma saatleri kaydedildi.")
                            st.session_state.pop("saat_taslak", None)
                            st.session_state.saat_ver = ver + 1
                            st.rerun()
                        except ApiError as e:
                            st.error(f"Kaydedilemedi: {e}")

    # --- Planlı duruşlar ---
    st.write("")
    with st.container(key="card_durus"):
        st.markdown('<div class="card-title">Planlı duruşlar</div>', unsafe_allow_html=True)
        st.caption("Tatil, bakım ve duruş dönemleri tüketim grafiklerinde işaretlenir.")
        try:
            duruslar = api.downtime(bid)
        except ApiError as e:
            st.error(f"Duruşlar alınamadı: {e}")
            duruslar = None

        if duruslar is not None:
            # Zamanlar bina yerel saati; karşılaştırma için şimdiki zamanı da o saate çeviriyoruz.
            tz = next((b["timezone"] for b in buildings if b["building_id"] == bid), "UTC")
            simdi = pd.Timestamp.now(tz=tz).tz_localize(None)
            gelecek = [d for d in duruslar if pd.Timestamp(d["ends_at"]) >= simdi]
            gecmis = [d for d in duruslar if pd.Timestamp(d["ends_at"]) < simdi]

            def sil_iste(did):
                st.session_state.sil_onay = did

            def sil(did):
                st.session_state.pop("sil_onay", None)
                try:
                    api.delete_downtime(bid, did)
                    st.session_state.ayar_msg = ("ok", "Duruş silindi.")
                except ApiError as e:
                    st.session_state.ayar_msg = ("err", f"Silinemedi: {e}")

            def durus_html(d):
                ad, ton = DURUS.get(d["kind"], (d["kind"], "data"))
                bas, bit = pd.Timestamp(d["starts_at"]), pd.Timestamp(d["ends_at"])
                aktif = bas <= simdi
                return (
                    f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>'
                    + ('<span class="badge sev-low">Devam ediyor</span>' if aktif else "")
                    + f'<span class="an-time">{tr_tarih_saat(bas)} → {tr_tarih_saat(bit)}</span></div>'
                    + (f'<div class="an-desc">{html.escape(d["note"])}</div>' if d["note"] else "")
                )

            if not gelecek:
                st.caption("Yaklaşan veya devam eden planlı duruş yok.")
            for d in gelecek:
                sol, sag = st.columns([5, 1.4], vertical_alignment="center")
                sol.markdown(durus_html(d), unsafe_allow_html=True)
                if not yazabilir:
                    continue
                if st.session_state.get("sil_onay") == d["id"]:
                    sag.button("Evet, sil", key=f"sil_ok_{d['id']}", type="primary", on_click=sil, args=(d["id"],))
                    sag.button("Vazgeç", key=f"sil_no_{d['id']}", on_click=sil_iste, args=(None,))
                else:
                    sag.button("Sil", key=f"sil_{d['id']}", icon=":material/delete:", on_click=sil_iste, args=(d["id"],))

            if gecmis:
                with st.expander(f"Geçmiş duruşlar ({len(gecmis)})"):
                    for d in reversed(gecmis):
                        st.markdown(durus_html(d), unsafe_allow_html=True)

            if yazabilir:
                st.write("")
                with st.form("durus_ekle", clear_on_submit=True):
                    st.markdown("**Yeni duruş ekle**")
                    tur = st.selectbox("Tür", list(DURUS), format_func=lambda k: DURUS[k][0])
                    bugun = simdi.date()
                    g1, g2, g3, g4 = st.columns(4)
                    bas_g = g1.date_input("Başlangıç tarihi", value=bugun, format="DD.MM.YYYY")
                    bas_s = g2.time_input("Başlangıç saati", value=dtime(0, 0), step=900)
                    bit_g = g3.date_input("Bitiş tarihi", value=bugun + timedelta(days=1), format="DD.MM.YYYY")
                    bit_s = g4.time_input("Bitiş saati", value=dtime(0, 0), step=900)
                    not_ = st.text_input("Not (isteğe bağlı)", max_chars=500, placeholder="Örn. yıllık kompresör bakımı")
                    if st.form_submit_button("Ekle", type="primary", icon=":material/add:"):
                        bas = datetime.combine(bas_g, bas_s)
                        bit = datetime.combine(bit_g, bit_s)
                        if bit <= bas:
                            st.error("Bitiş, başlangıçtan sonra olmalı.")
                        else:
                            try:
                                api.add_downtime(bid, bas.isoformat(), bit.isoformat(), tur, not_.strip() or None)
                                st.session_state.ayar_msg = ("ok", "Duruş eklendi.")
                                st.rerun()
                            except ApiError as e:
                                st.error(f"Eklenemedi: {e}")

    # --- Sistem günlüğü (yalnız admin) ---
    if me["role"] == "admin":
        st.write("")
        with st.container(key="card_gunluk"):
            st.markdown('<div class="card-title">Sistem günlüğü</div>', unsafe_allow_html=True)
            st.caption(
                "Panel sürecindeki son API hataları ve yavaş istekler (en fazla 100 kayıt, panel yeniden "
                "başlayınca sıfırlanır). Tam günlük: docker compose logs dashboard"
            )
            olaylar = list(EVENTS)
            say = {k: sum(1 for o in olaylar if o["seviye"] == k) for k in ("HATA", "UYARI", "YAVAŞ")}
            g1, g2, g3 = st.columns(3)
            g1.metric("Hata", say["HATA"])
            g2.metric("Uyarı (401/404)", say["UYARI"])
            g3.metric("Yavaş istek", say["YAVAŞ"])
            if olaylar:
                tablo = pd.DataFrame(reversed(olaylar))
                tablo["zaman"] = tablo["zaman"].map(lambda z: f"{z:%d.%m %H:%M:%S}")
                st.dataframe(tablo, hide_index=True, use_container_width=True, height=240)
            else:
                st.success("Kayıtlı hata veya yavaş istek yok.")
            if st.button("Önbelleği temizle ve verileri yeniden yükle", key="cache_temizle"):
                api._cache.clear()
                st.cache_data.clear()
                st.session_state.ayar_msg = ("ok", "Önbellek temizlendi.")
                st.rerun()
    st.stop()

# ---------- Tüketim Analizi sayfası (şimdilik bina listesi) ----------
if st.session_state.sayfa == "tuketim":
    st.markdown(
        '<span class="pg-okunur" hidden></span><div class="hello">Tüketim Analizi</div><div class="hello-sub">Sayaçları karşılaştırın</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    PAL = ["#2563EB", "#10B981", "#F59E0B", "#8B5CF6", "#EF4444"]
    bmap = {b["building_id"]: b for b in buildings}
    ilk3 = [b["building_id"] for b in sorted(buildings, key=lambda b: b["anomaly_count"], reverse=True)[:3]]

    k1, k2, k3 = st.columns([3, 1.2, 1.2])
    secili = k1.multiselect(
        "Karşılaştırılacak sayaçlar (en fazla 5)", ids, default=ilk3, max_selections=5,
        format_func=lambda i: names[i], key="kars_bina",
    )
    donem_k = k2.selectbox("Dönem", ["Son 24 saat", "Son 7 gün", "Son 30 gün"], index=1, key="kars_donem")
    esitle = k3.toggle("Ölçeği eşitle", key="kars_esitle", help="Her sayacın ortalaması 100 olacak şekilde gösterir; farklı büyüklükteki sayaçları kıyaslamak için.")
    k_hours = {"Son 24 saat": 24, "Son 7 gün": 168, "Son 30 gün": 720}[donem_k]

    if not secili:
        st.info("Karşılaştırmak için en az bir sayaç seçin.")
    else:
        veri = readings_multi(api, me["user_id"], tuple(secili), k_hours)
        if not veri:
            st.info("Seçilen sayaçlar için bu dönemde veri yok.")
        else:
            saatlik = k_hours == 24
            c_sol, c_sag = st.columns([2, 1])

            with c_sol:
                with st.container(key="card_kars_cizgi"):
                    st.markdown(
                        f'<div class="card-title">Tüketim karşılaştırması'
                        f'{" (ortalama = 100)" if esitle else " (kWh)"}</div>',
                        unsafe_allow_html=True,
                    )
                    fig, ax = plt.subplots(figsize=(9, 3.8))
                    fig.patch.set_alpha(0)
                    ax.set_facecolor("none")
                    for renk, i in zip(PAL, secili, strict=False):
                        if i not in veri:
                            continue
                        s = veri[i].set_index("ts")["kwh"].resample("h" if saatlik else "D").sum()
                        if not saatlik and len(s) > 2:
                            s = s.iloc[1:]  # ilk gün kısmi olduğu için düşük görünür
                        if esitle and s.mean():
                            s = s / s.mean() * 100
                        ax.plot(s.index, s.values, color=renk, lw=2, label=names[i])
                    for k in ("top", "right"):
                        ax.spines[k].set_visible(False)
                    for k in ("left", "bottom"):
                        ax.spines[k].set_color("#E2E8F0")
                    ax.grid(axis="y", color="#EEF2F7")
                    ax.tick_params(colors="#64748B", labelsize=9)
                    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M" if saatlik else "%d.%m"))
                    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
                    alt = (
                        f"Tüketim karşılaştırma grafiği: {', '.join(names[i] for i in secili if i in veri)}. "
                        "Sayısal değerler aşağıdaki karşılaştırma tablosunda."
                    )
                    st.markdown(fig_html(fig, "chart", alt), unsafe_allow_html=True)

            toplam = {i: float(veri[i]["kwh"].sum()) for i in secili if i in veri}
            with c_sag:
                with st.container(key="card_kars_bar"):
                    st.markdown('<div class="card-title">Toplam tüketim (kWh)</div>', unsafe_allow_html=True)
                    # Genel Bakış'taki "Sayaç Bazlı Tüketim" ile aynı HTML çubuklar: adlar kesilmez, yazı keskin kalır.
                    renk_map = {i: PAL[n] for n, i in enumerate(secili)}
                    sirali_k = sorted(toplam.items(), key=lambda x: -x[1])
                    en_buyuk_k = max(toplam.values()) or 1
                    toplam_k = sum(toplam.values()) or 1
                    st.markdown(
                        bar_rows(
                            [(renk_map[i], names[i], v, v / en_buyuk_k * 100, v / toplam_k * 100) for i, v in sirali_k]
                        ),
                        unsafe_allow_html=True,
                    )

            st.write("")
            with st.container(key="card_kars_tablo"):
                st.markdown('<div class="card-title">Karşılaştırma tablosu</div>', unsafe_allow_html=True)
                satirlar = []
                for i in secili:
                    if i not in toplam:
                        continue
                    n_saat = max(veri[i]["ts"].nunique(), 1)
                    satirlar.append(
                        {
                            "Sayaç": names[i],
                            "Kullanım": KULLANIM_TR.get(bmap[i]["primary_use"], bmap[i]["primary_use"] or "-"),
                            "Toplam (kWh)": round(toplam[i], 1),
                            "Saatlik ort. (kWh)": round(toplam[i] / n_saat, 2),
                            "Tahmini maliyet (TL)": round(toplam[i] * tarife[i], 2),
                            "Anomali (toplam)": bmap[i]["anomaly_count"],
                        }
                    )
                st.dataframe(pd.DataFrame(satirlar), use_container_width=True, hide_index=True,
                             column_config=SAYI_SUTUN)
            st.write("")
    with st.container(key="card_binalar"):
        st.markdown('<div class="card-title">Sayaçlar</div>', unsafe_allow_html=True)
        df = pd.DataFrame(
            {
                "Sayaç": [names[b["building_id"]] for b in buildings],
                "Kullanım": [KULLANIM_TR.get(b["primary_use"], b["primary_use"] or "-") for b in buildings],
                "Son puanlama": [(b["last_scored_ts"] or "-")[:16].replace("T", " ") for b in buildings],
                "Anomali": [b["anomaly_count"] for b in buildings],
            }
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.stop()

# ---------- Raporlar sayfası ----------
if st.session_state.sayfa == "rapor":
    st.markdown(
        '<span class="pg-okunur pg-rapor" hidden></span><div class="hello">Raporlar</div>'
        '<div class="hello-sub">Tasarruf özeti ve dışa aktarma (CSV, Excel, PDF)</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    r1, r2 = st.columns(2)
    donem_sec = r1.selectbox("Dönem", ["Son 7 gün", "Son 30 gün", "Son 90 gün"], index=2)
    kapsam = r2.selectbox("Kapsam", ["Seçili sayaç", "Tüm sayaçlar (en çok anomalili 20)"])
    r_hours = {"Son 7 gün": 168, "Son 30 gün": 720, "Son 90 gün": 2160}[donem_sec]
    if kapsam == "Seçili sayaç":
        r_items = ((bid, names[bid]),)
    else:
        en_cok_20 = sorted(buildings, key=lambda b: b["anomaly_count"], reverse=True)[:20]
        r_items = tuple(sorted((b["building_id"], names[b["building_id"]]) for b in en_cok_20))
    r_tarife = tuple((i, tarife[i]) for i, _ in r_items)
    rapor = report_data(api, me["user_id"], r_items, r_hours, r_tarife)
    o, r_bina, r_an = rapor["ozet"], rapor["binalar"], rapor["anomaliler"]
    st.caption(f"Dönem: {o['donem']} · {o['bina_sayisi']} sayaç")

    st.write("")
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(
        kpi_card(svg("zap", 24), "blue", "Toplam Enerji Tüketimi", f"{num(o['kwh'])} kWh", delta_html(o["kwh"], o["kwh_onceki"], "önceki döneme göre")),
        unsafe_allow_html=True,
    )
    k2.markdown(
        kpi_card(
            svg("coins", 24), "orange", "Tahmini Maliyet", tl(o["maliyet"]),
            f"<span>({num(o['tarife'], 2)} TL/kWh)</span>" if o["tarife"] is not None
            else "<span>(sayaç bazında tarife)</span>",
        ),
        unsafe_allow_html=True,
    )
    k3.markdown(
        kpi_card(svg("alert", 24), "red", "Tespit Edilen Anomali", str(o["anomali"]), f"<span>({donem_sec})</span>"),
        unsafe_allow_html=True,
    )
    k4.markdown(
        kpi_card(svg("leaf", 24), "green", "Tasarruf Potansiyeli", tl(o["tasarruf"]), "<span>(anomali kaynaklı)</span>"),
        unsafe_allow_html=True,
    )
    st.write("")

    with st.container(key="card_rapor_indir"):
        st.markdown('<div class="card-title">Dışa aktar</div>', unsafe_allow_html=True)
        dosya = f"rapor_{donem_sec.replace(' ', '_')}"
        e1, e2, e3 = st.columns(3)
        try:
            e1.download_button("CSV (anomaliler)", to_csv(rapor), f"{dosya}.csv", "text/csv",
                               icon=":material/download:", use_container_width=True)
            e2.download_button("Excel (özet + tablolar)", to_xlsx(rapor), f"{dosya}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               icon=":material/download:", use_container_width=True)
            e3.download_button("PDF (özet raporu)", to_pdf(rapor), f"{dosya}.pdf", "application/pdf",
                               icon=":material/download:", use_container_width=True)
        except Exception as e:
            st.error(f"Dosya hazırlanamadı: {e}")

    st.write("")
    with st.container(key="card_rapor_bina"):
        st.markdown('<div class="card-title">Sayaç bazlı özet</div>', unsafe_allow_html=True)
        if r_bina.empty:
            st.info("Bu dönem için veri yok.")
        else:
            st.dataframe(r_bina, use_container_width=True, hide_index=True, column_config=SAYI_SUTUN)

    st.write("")
    with st.container(key="card_rapor_anomali"):
        st.markdown('<div class="card-title">Tasarruf fırsatları (anomaliler)</div>', unsafe_allow_html=True)
        if r_an.empty:
            st.success("Bu dönemde anomali yok.")
        else:
            st.dataframe(r_an.drop(columns=["Bitiş"]), use_container_width=True, hide_index=True,
                         column_config=SAYI_SUTUN)
    st.stop()

if st.session_state.sayfa != "genel":
    st.info("Bu sayfa henüz hazır değil.")
    st.stop()

# ---------- Seçili aralık ve bina verisi ----------
st.session_state["_tam_calisma"] = time.time()  # otomatik yenileme sayacı

# Dönem seçicinin varsayılanı oturum durumunda: takvimdeki hızlı seçimler ve özel aralık da aynı anahtarı yazar.
if not st.session_state.get("aralik"):
    st.session_state.aralik = "Hafta"
aralik = st.session_state.aralik
ozel = aralik == "Özel"
hours = {"Gün": 24, "Hafta": 168, "Ay": 720, "Özel": 720}[aralik]
donem = {"Gün": "Son 24 saat", "Hafta": "Son 7 gün", "Ay": "Son 30 gün", "Özel": "Seçilen aralık"}[aralik]
karsilas = {
    "Gün": "Önceki güne göre", "Hafta": "Geçen haftaya göre",
    "Ay": "Geçen aya göre", "Özel": "Önceki döneme göre",
}[aralik]

try:
    raw = pd.DataFrame(api.readings(bid, 1440 if ozel else hours * 2))
    forecast = pd.DataFrame(api.forecast(bid, 720 if ozel else hours))
    anomalies = api.anomalies(bid, limit=50)
except ApiError as e:
    if e.status != 404:
        raise
    st.info(f"{names[bid]} sayacında henüz veri yok. Canlı demo sayaçları Canlı İzleme ekranından "
            "başlatılınca dolar.")
    st.stop()

son = None
cutoff = ust = None
cur = prev = raw
if not raw.empty:
    raw["ts"] = pd.to_datetime(raw["ts"], utc=True)
    son = raw["ts"].max()
    if ozel:
        rng = st.session_state.get("ozel_aralik")
        if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
            rng = ((son - timedelta(days=6)).date(), son.date())
        cutoff = pd.Timestamp(rng[0], tz="UTC")
        ust = pd.Timestamp(rng[1], tz="UTC") + timedelta(days=1)
        uzun = ust - cutoff
        cur = raw[(raw["ts"] >= cutoff) & (raw["ts"] < ust)]
        prev = raw[(raw["ts"] >= cutoff - uzun) & (raw["ts"] < cutoff)]
    else:
        cutoff = son - timedelta(hours=hours)
        cur = raw[raw["ts"] > cutoff]
        prev = raw[raw["ts"] <= cutoff]

if not forecast.empty:
    forecast["ts"] = pd.to_datetime(forecast["ts"], utc=True)
    if not cur.empty:
        forecast = forecast[(forecast["ts"] >= cur["ts"].min()) & (forecast["ts"] <= cur["ts"].max())]

kwh_cur = float(cur["kwh"].sum()) if not cur.empty else 0.0
kwh_prev = float(prev["kwh"].sum()) if not prev.empty else 0.0

win_anoms = []
if anomalies and cutoff is not None:
    win_anoms = [
        a for a in anomalies
        if pd.to_datetime(a["ts_start"], utc=True) > cutoff
        and (ust is None or pd.to_datetime(a["ts_start"], utc=True) < ust)
    ]
n_an = len(win_anoms)
tasarruf = sum((a["est_cost_tl"] or 0) for a in win_anoms)

# Anomali olayları ve tepe sapmaları: üst çubuktaki bildirimler ve ana grafik birlikte kullanır.
events = build_events(cur, forecast, win_anoms)
pct_by_id = {e["id"]: e["pct"] for e in events}

# ---------- Üst bar ----------
b_bil = next(b for b in buildings if b["building_id"] == bid)
sayac_bilgi = " · ".join(filter(None, [
    b_bil.get("tenant_name"), b_bil["name"] or b_bil["external_id"], b_bil.get("asset_name"),
    EKIPMAN_TR.get(b_bil.get("equipment_type")),
])) or "Enerji tüketim verilerini ve anomali durumlarını aşağıda görebilirsiniz."
aralik_txt = "-" if cur.empty else f"{tr_tarih(cur['ts'].min())} – {tr_tarih(cur['ts'].max())}"
h1, h3 = st.columns([1, 1.25], vertical_alignment="center")
with h1:
    st.markdown(
        '<div class="hello">Genel Bakış</div>'
        f'<div class="hello-sub">{html.escape(sayac_bilgi)}</div>',
        unsafe_allow_html=True,
    )
with h3:
    with st.container(horizontal=True, horizontal_alignment="right", gap="small", key="ust_sag"):
        # Tarih ve bildirimler aynı satırda, sağa yaslı.
        with st.container(key="tarih_box"):
            with st.popover(aralik_txt, icon=":material/calendar_month:", use_container_width=True):
                if son is None:
                    st.caption("Veri yok")
                else:
                    st.markdown('<div class="pop-title">Dönem seçin</div>', unsafe_allow_html=True)
                    with st.container(horizontal=True, gap="small", key="tarih_hizli"):
                        for etiket, kod in (("Son 24 saat", "Gün"), ("Son 7 gün", "Hafta"), ("Son 30 gün", "Ay")):
                            st.button(
                                etiket, key=f"hizli_{kod}", on_click=st.session_state.update, kwargs={"aralik": kod},
                                type="primary" if aralik == kod else "secondary",
                            )
                    st.date_input(
                        "Özel aralık",
                        value=((son - timedelta(days=6)).date(), son.date()),
                        min_value=(son - timedelta(days=59)).date(),
                        max_value=son.date(),
                        format="DD.MM.YYYY",
                        key="ozel_aralik",
                        on_change=lambda: st.session_state.update(aralik="Özel"),
                    )
                    st.caption(f"Veri {tr_tarih(son - timedelta(days=59))} – {tr_tarih(son)} arasında seçilebilir.")
        # Bildirimler: seçili binanın son anomalileri (gerçek veri). Rozet: tüm binalarda son 24 saatteki alarmlar.
        with st.container(key="bell_box"):
            with st.popover(str(summary["active_alerts"]), icon=":material/notifications:", help="Bildirimler"):
                st.markdown(
                    f'<div class="pop-title">Bildirimler</div><div class="pop-sub">Tüm sayaçlarda son 24 saatte '
                    f'<b>{summary["active_alerts"]}</b> alarm · aşağıda {html.escape(names[bid])}</div>',
                    unsafe_allow_html=True,
                )
                if not anomalies:
                    st.caption("Bu sayaç için bildirim yok.")
                satirlar = []
                for a in anomalies[:5]:
                    if a["category"] == "data_issue":
                        ton, ad = "data", "Veri sorunu"
                    else:
                        ad = severity(pct_by_id.get(a["id"]), a["est_cost_tl"])
                        ton = {"Yüksek": "high", "Orta": "mid", "Düşük": "low"}[ad]
                    yeni = '<i class="nt-new" title="Değerlendirilmedi"></i>' if a["verdict"] is None else ""
                    satirlar.append(
                        f'<div class="nt-item"><span class="nt-dot sev-{ton}"></span><div class="nt-body">'
                        f'<div class="nt-top"><span class="badge sev-{ton}">{ad}</span>{yeni}'
                        f'<span class="nt-time">{tr_tarih_saat(pd.to_datetime(a["ts_start"], utc=True))}</span></div>'
                        f'<div class="nt-desc">{html.escape(kisa_aciklama(a["explanation"]))}</div>'
                        f'<div class="nt-cost">Tahmini maliyet: <b>{tl(a["est_cost_tl"])}</b></div></div></div>'
                    )
                st.markdown(f'<div class="nt-list">{"".join(satirlar)}</div>', unsafe_allow_html=True)
                st.button(
                    "Tüm anomalileri gör", key="nt_hepsi", icon=":material/arrow_forward:",
                    on_click=set_page, args=("anomali",), use_container_width=True,
                )

st.write("")

# ---------- KPI kartları ----------
c1, c2, c3, c4 = st.columns(4)
c1.markdown(
    kpi_card(
        svg("zap", 24), "blue", "Toplam Enerji Tüketimi", f"{num(kwh_cur)} kWh", delta_html(kwh_cur, kwh_prev, karsilas)
    ),
    unsafe_allow_html=True,
)
c2.markdown(
    kpi_card(
        svg("coins", 24), "orange", "Tahmini Maliyet", tl(kwh_cur * tarife[bid]),
        delta_html(kwh_cur * tarife[bid], kwh_prev * tarife[bid], karsilas),
    ),
    unsafe_allow_html=True,
)
c3.markdown(
    kpi_card(svg("alert", 24), "red", "Tespit Edilen Anomali", str(n_an), f"<span>({donem}, bu sayaç)</span>"),
    unsafe_allow_html=True,
)
c4.markdown(
    kpi_card(svg("leaf", 24), "green", "Tasarruf Potansiyeli", tl(tasarruf), f"<span>({donem}, anomali kaynaklı)</span>"),
    unsafe_allow_html=True,
)

st.write("")

# ---------- Ana grafik kartı + anomali paneli ----------
acik, kapali, duruslar = (None, None, []) if cur.empty else takvim_bantlari(
    api, bid, cur["ts"].min(), cur["ts"].max() + timedelta(hours=1)
)
left, right = st.columns([2, 1])

with left:
    with st.container(key="chart_card"):
        t1, t2, t3 = st.columns([2.4, 2.9, 0.7])
        t1.markdown('<div class="card-title">Enerji Tüketim Grafiği</div>', unsafe_allow_html=True)
        with t2:
            st.segmented_control(
    "Aralık", ["Gün", "Hafta", "Ay", "Özel"], key="aralik", label_visibility="collapsed"
)
        with t3:
            csv = cur[["ts", "kwh"]].to_csv(index=False).encode("utf-8") if not cur.empty else b""
            st.download_button(
                "", csv, file_name=f"tuketim_{bid}.csv", mime="text/csv", icon=":material/download:", help="CSV olarak indir"
            )

        st.markdown(
            '<div class="legend">'
            '<span><i class="dot" style="background:#2563EB"></i>Gerçekleşen Tüketim</span>'
            '<span><i class="ln"></i>Beklenen Tüketim</span>'
            '<span><i class="dot" style="background:#DC2626"></i>Anomali</span>'
            f'{bant_lejant(kapali, duruslar)}</div>',
            unsafe_allow_html=True,
        )
        if cur.empty:
            st.info("Bu aralıkta ölçüm yok.")
        else:
            fig = consumption_chart(cur, forecast, events, aralik, kapali, duruslar)
            st.markdown(fig_html(fig, "chart", chart_alt(cur, forecast, events, duruslar)), unsafe_allow_html=True)
            md = mesai_disi(cur, acik)
            if md and md[0] > 0:
                st.caption(
                    f"Çalışma saatleri dışında **{num(md[0])} kWh** tüketildi (toplamın %{num(md[1])}'i, "
                    f"≈ {tl(md[0] * tarife[bid])})."
                )
            elif acik is None:
                st.caption("Çalışma saatleri tanımlı değil; Ayarlar'dan vardiya ekleyerek mesai dışı tüketimi görebilirsiniz.")
            grafik_tablosu(cur, forecast)

with right:
    st.markdown(anomaly_panel(anomalies[:8], names[bid], pct_by_id), unsafe_allow_html=True)
    st.button("Tüm anomalileri gör →", key="tumunu_gor", on_click=set_page, args=("anomali",), use_container_width=True)

# ---------- Alt satır: 3 kart ----------
PALET = ["#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#06B6D4", "#94A3B8"]
MAX_BINA = 20

sirali = sorted(buildings, key=lambda b: b["anomaly_count"], reverse=True)[:MAX_BINA]
ids_tuple = tuple(sorted(b["building_id"] for b in sirali))
kwh_map = kwh_by_building(api, me["user_id"], ids_tuple, hours)

rows = sorted(((names[i], v) for i, v in kwh_map.items() if v > 0), key=lambda x: -x[1])
dagilim = rows[:5]
if rows[5:]:
    dagilim.append(("Diğer", sum(v for _, v in rows[5:])))
toplam_kwh = sum(v for _, v in dagilim)

sev_say = {"Yüksek": 0, "Orta": 0, "Düşük": 0}
for a in win_anoms:
    if a["category"] == "data_issue":
        continue
    sev_say[severity(pct_by_id.get(a["id"]), a["est_cost_tl"])] += 1
toplam_uyari = sum(sev_say.values())
SEV_RENK = {"Yüksek": "#DC2626", "Orta": "#B45309", "Düşük": "#047857"}  # rozetlerle aynı (AA kontrast)

st.write("")
b1, b2, b3 = st.columns(3)

with b1:
    with st.container(key="card_dagilim"):
        st.markdown('<div class="card-title">Tüketimin Dağılımı</div>', unsafe_allow_html=True)
        # Uyarı kartındaki alt başlıkla aynı satır: üç kartın başlıkları ve halkalar hizalı durur.
        st.caption(f"{len(rows)} sayaç · {donem}")
        if toplam_kwh <= 0:
            st.info("Dağılım için veri yok.")
        else:
            # Halka üstte ve büyük, lejant altında iki sütun: kartın boş kalan alanını kullanır.
            fig = donut_chart([v for _, v in dagilim], PALET[: len(dagilim)], num(toplam_kwh), "kWh")
            alt = f"Tüketim dağılımı halka grafiği, toplam {num(toplam_kwh)} kWh; " + "; ".join(
                f"{n} %{num(v / toplam_kwh * 100)}" for n, v in dagilim
            )
            lejant = legend_rows([(PALET[k], n, f"%{num(v / toplam_kwh * 100)}") for k, (n, v) in enumerate(dagilim)])
            st.markdown(fig_html(fig, alt=alt) + f'<div class="lg-grid">{lejant}</div>', unsafe_allow_html=True)

with b2:
    with st.container(key="card_bina"):
        st.markdown('<div class="card-title">Sayaç Bazlı Tüketim</div>', unsafe_allow_html=True)
        st.caption(f"En çok tüketen {min(len(rows), 5)} sayaç · {donem}")
        if toplam_kwh <= 0:
            st.info("Dağılım için veri yok.")
        else:
            en_buyuk = max(v for _, v in dagilim)
            st.markdown(
                bar_rows(
                    [(PALET[k], n, v, v / en_buyuk * 100, v / toplam_kwh * 100) for k, (n, v) in enumerate(dagilim)]
                ),
                unsafe_allow_html=True,
            )

with b3:
    with st.container(key="card_uyari"):
        st.markdown('<div class="card-title">Uyarı Durumları</div>', unsafe_allow_html=True)
        st.caption(f"{names[bid]} · {donem}")
        fig = donut_chart([sev_say[k] for k in SEV_RENK], list(SEV_RENK.values()), str(toplam_uyari), "Toplam Uyarı")
        alt = f"Uyarı durumları halka grafiği, toplam {toplam_uyari} uyarı; " + "; ".join(
            f"{k} {sev_say[k]}" for k in SEV_RENK
        )
        lejant = legend_rows(
            [
                (SEV_RENK[k], k, f"{sev_say[k]} (%{num(sev_say[k] / toplam_uyari * 100) if toplam_uyari else 0})")
                for k in SEV_RENK
            ]
        )
        st.markdown(fig_html(fig, alt=alt) + f'<div class="lg-grid c3">{lejant}</div>', unsafe_allow_html=True)

# ---------- Alt durum çubuğu ----------
try:
    saglik = api.health()
except Exception:
    saglik = None

son_veri = "-" if cur.empty else tr_tarih_saat(cur["ts"].max())
if saglik:
    kaynak = {"demo": "demo verisi", "database": "veritabanı"}.get(saglik.get("mode"), saglik.get("mode"))
    durum = f'<span class="st-dot"></span>Sistem aktif · Son güncelleme: {son_veri}'
    st_ = api.stats
    sag = (
        f"Veri kaynağı: {kaynak} · Sayfa {num((time.perf_counter() - sayfa_t0) * 1000)} ms · "
        f"{st_['calls']} API çağrısı, {st_['cached']} önbellekten"
    )
else:
    durum = '<span class="st-dot off"></span>API yanıt vermiyor'
    sag = ""
st.markdown(f'<div class="statusbar"><span>{durum}</span><span>{sag}</span></div>', unsafe_allow_html=True)

# ---------- Otomatik yenileme ----------
@st.fragment(run_every=60)
def canli_yenile():
    a, b = st.columns([1.2, 3])
    a.toggle("Otomatik yenile (60 sn)", value=True, key="oto")
    t = datetime.fromtimestamp(st.session_state["_tam_calisma"], timezone(timedelta(hours=3)))
    b.caption(f"Son güncelleme: {t:%H:%M:%S}")
    if st.session_state.get("oto", True) and time.time() - st.session_state["_tam_calisma"] >= 55:
        st.rerun()


canli_yenile()