import base64
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
TARIFF_TL = 3.5  # API sayacın tarifesini vermezse kullanılan varsayılan (TL/kWh)

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
        body = '<div class="an-empty">Bu bina için anomali yok.</div>'
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
                f'<div class="an-cost">Tahmini maliyet etkisi: <b>{tl(a["est_cost_tl"])}</b></div>'
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


def kisa_aciklama(metin, n=110):
    """Bildirim için açıklama: baştaki "2016-12-30 01:00-06:00:" tarih öneki atılır (saat zaten ayrıca yazılı)."""
    metin = re.sub(r"^\d{4}-\d{2}-\d{2} [\d:]+(-[\d:]+)?:\s*", "", metin or "Beklenmeyen tüketim")
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


@st.cache_data(ttl=600, show_spinner="Binalar yükleniyor...")
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
    st.markdown(
        """<style>
        .stApp{background:radial-gradient(1100px 560px at 8% -10%,#DBEAFE 0%,transparent 60%),
               radial-gradient(900px 500px at 100% 110%,#D1FAE5 0%,transparent 55%),#F4F7FC;}
        [data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="collapsedControl"]{display:none !important;}
        .block-container{zoom:1 !important;max-width:1120px !important;padding:6vh 1rem 2rem !important;}
        .st-key-login_wrap{background:#fff;border-radius:24px;overflow:hidden;padding:0 !important;gap:0 !important;
               box-shadow:0 30px 80px rgba(15,23,42,.14),0 0 0 1px rgba(15,23,42,.04);}
        .st-key-login_wrap [data-testid="stHorizontalBlock"]{gap:0 !important;align-items:stretch !important;}
        .st-key-login_wrap [data-testid="stColumn"]:first-child > div{height:100%;}
        .st-key-login_wrap [data-testid="stColumn"]:last-child{padding:40px 56px 32px !important;
               display:flex;flex-direction:column;justify-content:center;}
        .st-key-login_wrap [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"]{
               flex:0 0 auto !important;height:auto !important;}
        .st-key-login_wrap [data-testid="stForm"]{border:0 !important;padding:0 !important;}

        /* Giriş alanları: çerçeve Streamlit'in kök öğesine verilir, iç katmanlar şeffaf */
        .st-key-login_wrap [data-testid="stTextInputRootElement"]{
            border:1.5px solid #CBD5E1 !important;border-radius:12px !important;background:#F8FAFC !important;
            min-height:50px;transition:border-color .15s, box-shadow .15s, background .15s;}
        .st-key-login_wrap [data-testid="stTextInputRootElement"]:hover{border-color:#94A3B8 !important;}
        .st-key-login_wrap [data-testid="stTextInputRootElement"]:focus-within{
            border-color:#2563EB !important;background:#fff !important;box-shadow:0 0 0 4px rgba(37,99,235,.14);}
        .st-key-login_wrap [data-testid="stTextInputRootElement"] *{background:transparent !important;border:0 !important;}
        .st-key-login_wrap [data-testid="stTextInputRootElement"] input{font-size:15px;color:#0F172A;padding-left:4px;}
        .st-key-login_wrap [data-testid="stTextInputRootElement"] input:focus-visible{outline:none !important;}
        .st-key-login_wrap [data-testid="stTextInputRootElement"] [data-testid="stIconMaterial"]{color:#64748B;}
        .st-key-login_wrap label p{font-size:13.5px;font-weight:600;color:#334155;}

        .st-key-login_wrap [data-testid="stFormSubmitButton"]{margin-top:10px;}
        .st-key-login_wrap [data-testid="stFormSubmitButton"] button{
            min-height:52px;border:0;border-radius:12px;color:#fff;
            background:linear-gradient(135deg,#2563EB,#1D4ED8);box-shadow:0 10px 24px rgba(37,99,235,.28);
            transition:transform .15s, box-shadow .15s, filter .15s;}
        .st-key-login_wrap [data-testid="stFormSubmitButton"] button p{font-size:16px;font-weight:600;}
        .st-key-login_wrap [data-testid="stFormSubmitButton"] button:hover{filter:brightness(1.07);color:#fff;
            transform:translateY(-1px);box-shadow:0 14px 28px rgba(37,99,235,.32);}
        .st-key-login_wrap [data-testid="stFormSubmitButton"] button:active{transform:none;}

        /* Sol panel */
        .lg-left{position:relative;height:100%;min-height:620px;box-sizing:border-box;padding:40px 44px 36px;color:#fff;
            display:flex;flex-direction:column;overflow:hidden;
            background:radial-gradient(420px 320px at 90% 0%,rgba(74,222,128,.20),transparent 70%),
                       radial-gradient(380px 300px at 0% 100%,rgba(59,130,246,.30),transparent 70%),
                       linear-gradient(160deg,#0B1222 0%,#172554 100%);}
        .lg-left::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
            background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),
                             linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);
            background-size:32px 32px;mask-image:linear-gradient(180deg,#000 0%,transparent 75%);
            -webkit-mask-image:linear-gradient(180deg,#000 0%,transparent 75%);}
        .lg-left > *{position:relative;}
        .lg-brand{display:flex;align-items:center;gap:12px;}
        .lg-brand b{display:block;font-size:22px;font-weight:700;letter-spacing:-.3px;line-height:1.1;}
        .lg-brand span{display:block;font-size:12.5px;color:#94A3B8;margin-top:2px;}

        .lg-preview{margin:34px 0 30px;padding:18px 18px 14px;border-radius:16px;
            background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
            box-shadow:0 20px 40px rgba(0,0,0,.25);}
        .lg-prev-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;}
        .lg-prev-k{font-size:12px;color:#94A3B8;text-transform:uppercase;letter-spacing:.6px;font-weight:600;}
        .lg-prev-v{font-size:24px;font-weight:700;margin-top:4px;letter-spacing:-.4px;}
        .lg-prev-v small{font-size:13px;font-weight:600;color:#94A3B8;margin-left:4px;}
        .lg-pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:5px 10px;
            border-radius:999px;background:rgba(248,113,113,.16);color:#FCA5A5;border:1px solid rgba(248,113,113,.30);
            white-space:nowrap;}
        .lg-pill i{width:7px;height:7px;border-radius:50%;background:#F87171;box-shadow:0 0 0 3px rgba(248,113,113,.25);}
        .lg-preview svg.spark{display:block;width:100%;height:96px;margin-top:10px;}
        .lg-prev-leg{display:flex;gap:16px;font-size:12px;color:#94A3B8;margin-top:6px;}
        .lg-prev-leg span{display:inline-flex;align-items:center;gap:6px;}
        .lg-prev-leg .a{width:14px;border-top:2px solid #60A5FA;}
        .lg-prev-leg .b{width:14px;border-top:2px dashed #94A3B8;}

        .lg-hero h1{font-size:32px;line-height:1.15;font-weight:800;letter-spacing:-.8px;margin:0 0 12px;color:#fff;padding:0;}
        .lg-hero p{font-size:15px;line-height:1.6;color:#CBD5E1;margin:0;max-width:390px;}
        .lg-feats{list-style:none;margin:26px 0 0;padding:0;display:flex;flex-direction:column;gap:12px;}
        .lg-feats li{display:flex;align-items:center;gap:12px;font-size:14.5px;color:#E2E8F0;margin:0;}
        .lg-feats li i{width:34px;height:34px;border-radius:10px;background:rgba(255,255,255,.10);
            border:1px solid rgba(255,255,255,.10);display:inline-flex;align-items:center;justify-content:center;flex:none;}

        /* Sağ panel */
        .lg-status{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600;color:#047857;
            background:#ECFDF5;border:1px solid #A7F3D0;padding:5px 11px;border-radius:999px;margin-bottom:26px;}
        .lg-status i{width:8px;height:8px;border-radius:50%;background:#10B981;box-shadow:0 0 0 3px rgba(16,185,129,.2);}
        .lg-status.off{color:#B91C1C;background:#FEF2F2;border-color:#FECACA;}
        .lg-status.off i{background:#EF4444;box-shadow:0 0 0 3px rgba(239,68,68,.2);}
        .lg-title{font-size:28px;font-weight:800;letter-spacing:-.6px;color:#0F172A;margin-bottom:6px;line-height:1.2;}
        .lg-sub{font-size:15px;color:#64748B;margin-bottom:24px;}
        .lg-secure{display:flex;align-items:center;gap:10px;margin-top:22px;padding-top:18px;border-top:1px solid #EEF2F7;
            font-size:13px;color:#64748B;line-height:1.45;}
        .lg-secure i{width:32px;height:32px;border-radius:9px;background:#EFF6FF;color:#2563EB;flex:none;
            display:inline-flex;align-items:center;justify-content:center;}
        .lg-foot{text-align:center;font-size:12.5px;color:#64748B;margin-top:22px;}

        @media (max-width:768px){
            .block-container{padding:1.25rem .75rem 1.5rem !important;}
            .lg-left{min-height:auto;padding:24px 22px;}
            .lg-preview,.lg-feats,.lg-hero p{display:none;}
            .lg-hero{margin-top:18px;}.lg-hero h1{font-size:22px;margin:0;}
            .st-key-login_wrap [data-testid="stColumn"]:last-child{padding:26px 22px !important;}
            .lg-status{margin-bottom:18px;}
        }
        @media (prefers-reduced-motion: reduce){
            .st-key-login_wrap [data-testid="stFormSubmitButton"] button:hover{transform:none;}
        }
        </style>""",
        unsafe_allow_html=True,
    )

    def _oz(ikon, metin):
        return f'<li><i>{svg(ikon, 18, "#FFFFFF")}</i>{metin}</li>'

    # Ürün önizlemesi: gerçek veri değil, temsili bir çizim; ekran okuyucudan gizli.
    egri = (
        "M0 70 L20 66 L40 68 L60 58 L80 60 L100 50 L120 54 L140 46 L160 52 L180 48 L200 40 "
        "L210 18 L222 12 L236 30 L250 50 L270 46 L290 52 L320 44"
    )
    onizleme = (
        '<div class="lg-preview" aria-hidden="true">'
        '<div class="lg-prev-top"><div><div class="lg-prev-k">Anlık tüketim</div>'
        '<div class="lg-prev-v">1.284<small>kWh</small></div></div>'
        '<span class="lg-pill"><i></i>Anomali tespit edildi</span></div>'
        '<svg class="spark" viewBox="0 0 320 96" preserveAspectRatio="none">'
        '<defs><linearGradient id="lgf" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#60A5FA" stop-opacity=".35"/>'
        '<stop offset="1" stop-color="#60A5FA" stop-opacity="0"/></linearGradient></defs>'
        '<rect x="196" y="0" width="44" height="96" fill="#F87171" opacity=".12"/>'
        '<path d="M0 72 L40 70 L80 64 L120 58 L160 56 L200 50 L240 50 L280 50 L320 50" fill="none" '
        'stroke="#94A3B8" stroke-width="1.5" stroke-dasharray="4 4"/>'
        f'<path d="{egri} L320 96 L0 96 Z" fill="url(#lgf)"/>'
        f'<path d="{egri}" fill="none" stroke="#60A5FA" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        '<circle cx="222" cy="12" r="4.5" fill="#F87171" stroke="#0B1222" stroke-width="2"/></svg>'
        '<div class="lg-prev-leg"><span><i class="a"></i>Gerçekleşen</span><span><i class="b"></i>Beklenen</span></div>'
        "</div>"
    )
    sol_html = (
        '<div class="lg-left">'
        f'<div class="lg-brand">{LOGO}<div><b>{BRAND}</b><span>{BRAND_SUB}</span></div></div>'
        + onizleme
        + '<div class="lg-hero"><h1>Enerji tüketiminizi tek ekrandan yönetin.</h1>'
        "<p>Sayaçlarınızı canlıya yakın izleyin, beklenmeyen tüketimi model tahminiyle karşılaştırarak erken "
        "yakalayın ve tasarruf fırsatlarını raporlayın.</p></div>"
        '<ul class="lg-feats">'
        + _oz("zap", "Beklenen ve gerçekleşen tüketim karşılaştırması")
        + _oz("alert", "Maliyeti ve önerisiyle anomali uyarıları")
        + _oz("file", "CSV, Excel ve PDF tasarruf raporları")
        + "</ul></div>"
    )

    try:
        api_acik = bool(api.health())
    except ApiError:
        api_acik = False
    durum_html = (
        '<div class="lg-status"><i></i>Tüm sistemler çalışıyor</div>'
        if api_acik
        else '<div class="lg-status off"><i></i>Sunucuya şu an ulaşılamıyor</div>'
    )

    giris_ok = False
    with st.container(key="login_wrap"):
        sol, sag = st.columns([1.1, 1], gap="small")
        sol.markdown(sol_html, unsafe_allow_html=True)
        with sag:
            st.markdown(
                durum_html
                + '<div class="lg-title">Hesabınıza giriş yapın</div>'
                '<div class="lg-sub">Devam etmek için kurumsal e-posta adresinizi ve şifrenizi girin.</div>',
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
                f'<div class="lg-foot">Giriş yapamıyorsanız sistem yöneticinize başvurun · © {datetime.now().year} {BRAND}</div>',
                unsafe_allow_html=True,
            )
    if giris_ok:
        st.rerun()
    st.stop()

me = st.session_state.me
who = me.get("name") or me["email"]

# ---------- Temel veri ----------
try:
    summary = api.summary()
    buildings = api.buildings()
except ApiError as e:
    if e.status == 401:
        st.session_state.clear()
        st.rerun()
    st.error(f"Veri alınamadı: {e}")
    st.stop()

if not buildings:
    st.warning("Henüz bina verisi yok.")
    st.stop()

names = {b["building_id"]: (b["name"] or b["external_id"]) for b in buildings}
# Her sayacın kendi tarifesi; eski API alanı vermezse varsayılan.
tarife = {b["building_id"]: b.get("tariff_tl_per_kwh") or TARIFF_TL for b in buildings}
ids = list(names)
en_cok = max(range(len(buildings)), key=lambda i: buildings[i]["anomaly_count"])

# ---------- Sol menü ----------
PAGES = [
    ("genel", ":material/home:", "Genel Bakış"),
    ("tuketim", ":material/bar_chart:", "Tüketim Analizi"),
    ("anomali", ":material/warning:", "Anomaliler"),
    ("rapor", ":material/description:", "Raporlar"),
    ("ayar", ":material/settings:", "Ayarlar"),
]
if "sayfa" not in st.session_state:
    st.session_state.sayfa = "genel"


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
        bid = st.selectbox("Bina", ids, index=en_cok, format_func=lambda i: names[i], key="bina")
        if st.button("Çıkış"):
            try:
                api.logout()
            finally:
                st.session_state.clear()
                st.rerun()

# ---------- Anomaliler sayfası ----------
if st.session_state.sayfa == "anomali":
    st.markdown(
        '<div class="hello">Anomaliler</div>'
        f'<div class="hello-sub">{html.escape(names[bid])} · son kayıtlar</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    try:
        tum = api.anomalies(bid, limit=100)
    except ApiError as e:
        st.error(f"Anomaliler alınamadı: {e}")
        st.stop()

    yazabilir = me["role"] in ("admin", "operator")
    KAT = {"waste": ("İsraf", "mid"), "data_issue": ("Veri sorunu", "data")}
    VER = {"confirmed": ("Onaylandı", "low"), "false_alarm": ("Yanlış alarm", "data"), "planned": ("Planlı", "blue")}
    # Karar düğmeleri: (kod, etiket, simge). Renkleri style.css'te anahtar adına göre verilir.
    KARAR = [
        ("confirmed", "Onayla", ":material/check_circle:"),
        ("false_alarm", "Yanlış alarm", ":material/block:"),
        ("planned", "Planlı", ":material/event_available:"),
    ]

    def gonder(aid, verdict, not_key=None):
        note = (st.session_state.get(not_key) or "").strip() if not_key else ""
        try:
            api.feedback(aid, verdict, note or None)
            if not_key:
                st.session_state[not_key] = ""
            st.session_state.fb_msg = ("ok", "Geri bildirim kaydedildi.")
        except ApiError as e:
            st.session_state.fb_msg = ("err", f"Kaydedilemedi: {e}")

    msg = st.session_state.pop("fb_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])

    def ac_detay(aid):
        st.session_state.detay_id = aid

    def kapat_detay():
        st.session_state.pop("detay_id", None)

    secili = None
    if st.session_state.get("detay_id") is not None:
        secili = next((x for x in tum if x["id"] == st.session_state.detay_id), None)
        if secili is None:
            kapat_detay()

    if secili is not None:
        a = secili
        s = pd.to_datetime(a["ts_start"], utc=True)
        e = pd.to_datetime(a["ts_end"], utc=True)
        ad, ton = KAT.get(a["category"], ("Diğer", "data"))
        v = VER.get(a["verdict"])
        vb = f'<span class="badge sev-{v[1]}">{v[0]}</span>' if v else '<span class="badge sev-wait">Bekliyor</span>'

        st.button("Listeye dön", icon=":material/arrow_back:", on_click=kapat_detay, key="detay_geri")
        st.markdown(
            '<div class="hello">Anomali Detayı</div>'
            f'<div class="hello-sub">{html.escape(names[bid])} · {tr_tarih_saat(s)} → {tr_tarih_saat(e)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{vb}</div>', unsafe_allow_html=True)

        try:
            pay = timedelta(hours=36)
            son_saat = pd.Timestamp(a["ts_end"]) + pay
            saat = int((son_saat - pd.Timestamp(a["ts_start"]) + pay) / timedelta(hours=1)) + 1
            ham = pd.DataFrame(api.readings(bid, min(saat, 2160), son_saat.isoformat()))
            tah = pd.DataFrame(api.forecast(bid, min(saat, 2160), son_saat.isoformat()))
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
                    api, bid, pencere["ts"].min(), pencere["ts"].max() + timedelta(hours=1)
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
                f'<div class="an-desc">{html.escape(a["explanation"] or "-")}</div>'
                + (f'<div class="an-advice">{svg("bulb", 17)}<span>{html.escape(a["advice"])}</span></div>' if a["advice"] else ""),
                unsafe_allow_html=True,
            )

        st.write("")
        with st.container(key="card_detay_geri"):
            st.markdown('<div class="card-title">Değerlendirme</div>', unsafe_allow_html=True)
            # API şimdilik notu döndürmüyor; alan eklenince burada görünür.
            if a.get("note"):
                st.markdown(
                    f'<div class="an-note">{svg("message", 16)}<span>{html.escape(a["note"])}</span></div>',
                    unsafe_allow_html=True,
                )
            if not yazabilir:
                st.caption("Değerlendirme yapmak için operatör veya yönetici rolü gerekir.")
            else:
                if v:
                    st.caption(f"Mevcut karar: {v[0]}. Yeni bir karar verirseniz son karar geçerli olur.")
                nk = f"d_not_{a['id']}"
                st.text_area(
                    "Not (isteğe bağlı)", key=nk, max_chars=1000, height=90,
                    placeholder="Örn. kompresör arızası, bakım ekibine iletildi.",
                )
                with st.container(horizontal=True, gap="small"):
                    for kod, etiket, ikon in KARAR:
                        st.button(
                            etiket, key=f"d_{kod}_{a['id']}", icon=ikon, on_click=gonder, args=(a["id"], kod, nk),
                            type="primary" if a["verdict"] == kod else "secondary",
                        )
        st.stop()

    bekleyen = sum(1 for a in tum if a["verdict"] is None)
    toplam_maliyet = sum((a["est_cost_tl"] or 0) for a in tum)
    k1, k2, k3 = st.columns(3)
    k1.markdown(
        kpi_card(svg("alert", 24), "red", "Toplam Kayıt", str(len(tum)), "<span>(son 100 kayıt içinde)</span>"),
        unsafe_allow_html=True,
    )
    k2.markdown(
        kpi_card(svg("bell", 24), "orange", "Geri Bildirim Bekleyen", str(bekleyen), "<span>(henüz değerlendirilmemiş)</span>"),
        unsafe_allow_html=True,
    )
    k3.markdown(
        kpi_card(svg("coins", 24), "blue", "Tahmini Maliyet Etkisi", tl(toplam_maliyet), "<span>(listelenen kayıtlar)</span>"),
        unsafe_allow_html=True,
    )
    st.write("")

    f1, f2 = st.columns(2)
    kat_f = f1.selectbox("Tür", ["Tümü", "İsraf", "Veri sorunu"])
    ver_f = f2.selectbox("Durum", ["Tümü", "Bekleyen", "Onaylandı", "Yanlış alarm", "Planlı"])

    def uygun(a):
        if kat_f != "Tümü" and KAT.get(a["category"], ("-", ""))[0] != kat_f:
            return False
        if ver_f == "Tümü":
            return True
        if ver_f == "Bekleyen":
            return a["verdict"] is None
        return a["verdict"] in VER and VER[a["verdict"]][0] == ver_f

    liste = [a for a in tum if uygun(a)]
    st.caption(f"{len(liste)} kayıt" + (" (ilk 25 gösteriliyor)" if len(liste) > 25 else ""))
    if not liste:
        st.info("Filtreye uyan anomali yok.")

    for a in liste[:25]:
        ad, ton = KAT.get(a["category"], ("Diğer", "data"))
        v = VER.get(a["verdict"])
        vb = f'<span class="badge sev-{v[1]}">{v[0]}</span>' if v else '<span class="badge sev-wait">Bekliyor</span>'
        bas = tr_tarih_saat(pd.to_datetime(a["ts_start"], utc=True))
        bit = tr_tarih_saat(pd.to_datetime(a["ts_end"], utc=True))
        fazla = f"{num(a['excess_kwh'], 1)} kWh" if a["excess_kwh"] is not None else "-"
        tavsiye = (
            f'<div class="an-advice">{svg("bulb", 17)}<span>{html.escape(a["advice"])}</span></div>' if a["advice"] else ""
        )
        with st.container(key=f"anom_{a['id']}"):
            st.markdown(
                f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{vb}'
                f'<span class="an-time">{bas} → {bit}</span></div>'
                f'<div class="an-desc">{html.escape(a["explanation"] or "-")}</div>'
                f'<div class="an-cost">Fazla tüketim: <b>{fazla}</b> · '
                f'Tahmini maliyet etkisi: <b>{tl(a["est_cost_tl"])}</b></div>{tavsiye}',
                unsafe_allow_html=True,
            )
            with st.container(horizontal=True, gap="small", key=f"anbtn_{a['id']}"):
                st.button("Detay", key=f"dt_{a['id']}", icon=":material/visibility:", on_click=ac_detay, args=(a["id"],))
                if yazabilir:
                    for kod, etiket, ikon in KARAR:
                        st.button(
                            etiket, key=f"{kod}_{a['id']}", icon=ikon, on_click=gonder, args=(a["id"], kod),
                            type="primary" if a["verdict"] == kod else "secondary",
                        )
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
            "Model, bu saatlerin dışındaki tüketimi daha sıkı izler. Kapanış saati açılıştan önceyse "
            "(örn. 22:00 → 06:00) vardiya gece yarısını geçer."
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
        st.caption("Tatil, bakım ve duruş dönemlerindeki düşük tüketim anomali sayılmaz.")
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
        '<span class="pg-okunur" hidden></span><div class="hello">Tüketim Analizi</div><div class="hello-sub">Tüm binalar</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    PAL = ["#2563EB", "#10B981", "#F59E0B", "#8B5CF6", "#EF4444"]
    bmap = {b["building_id"]: b for b in buildings}
    ilk3 = [b["building_id"] for b in sorted(buildings, key=lambda b: b["anomaly_count"], reverse=True)[:3]]

    k1, k2, k3 = st.columns([3, 1.2, 1.2])
    secili = k1.multiselect(
        "Karşılaştırılacak binalar (en fazla 5)", ids, default=ilk3, max_selections=5,
        format_func=lambda i: names[i], key="kars_bina",
    )
    donem_k = k2.selectbox("Dönem", ["Son 24 saat", "Son 7 gün", "Son 30 gün"], index=1, key="kars_donem")
    esitle = k3.toggle("Ölçeği eşitle", key="kars_esitle", help="Her binanın ortalaması 100 olacak şekilde gösterir; farklı büyüklükteki binaları kıyaslamak için.")
    k_hours = {"Son 24 saat": 24, "Son 7 gün": 168, "Son 30 gün": 720}[donem_k]

    if not secili:
        st.info("Karşılaştırmak için en az bir bina seç.")
    else:
        veri = readings_multi(api, me["user_id"], tuple(secili), k_hours)
        if not veri:
            st.info("Seçilen binalar için bu dönemde veri yok.")
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
                    # Genel Bakış'taki "Bina Bazlı Tüketim" ile aynı HTML çubuklar: adlar kesilmez, yazı keskin kalır.
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
                            "Bina": names[i],
                            "Kullanım": bmap[i]["primary_use"] or "-",
                            "Toplam (kWh)": round(toplam[i], 1),
                            "Saatlik ort. (kWh)": round(toplam[i] / n_saat, 2),
                            "Tahmini maliyet (TL)": round(toplam[i] * tarife[i], 2),
                            "Anomali (toplam)": bmap[i]["anomaly_count"],
                        }
                    )
                st.dataframe(pd.DataFrame(satirlar), use_container_width=True, hide_index=True)
            st.write("")
    with st.container(key="card_binalar"):
        st.markdown('<div class="card-title">Binalar</div>', unsafe_allow_html=True)
        df = pd.DataFrame(
            {
                "Bina": [names[b["building_id"]] for b in buildings],
                "Kullanım": [b["primary_use"] or "-" for b in buildings],
                "Son puanlama": [(b["last_scored_ts"] or "-")[:16].replace("T", " ") for b in buildings],
                "Anomali": [b["anomaly_count"] for b in buildings],
            }
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.stop()

# ---------- Raporlar sayfası ----------
if st.session_state.sayfa == "rapor":
    st.markdown(
        '<span class="pg-okunur" hidden></span><div class="hello">Raporlar</div>'
        '<div class="hello-sub">Tasarruf özeti ve dışa aktarma (CSV, Excel, PDF)</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    r1, r2 = st.columns(2)
    donem_sec = r1.selectbox("Dönem", ["Son 7 gün", "Son 30 gün"])
    kapsam = r2.selectbox("Kapsam", ["Seçili bina", "Tüm binalar (en çok anomalili 20)"])
    r_hours = {"Son 7 gün": 168, "Son 30 gün": 720}[donem_sec]
    if kapsam == "Seçili bina":
        r_items = ((bid, names[bid]),)
    else:
        en_cok_20 = sorted(buildings, key=lambda b: b["anomaly_count"], reverse=True)[:20]
        r_items = tuple(sorted((b["building_id"], names[b["building_id"]]) for b in en_cok_20))
    r_tarife = tuple((i, tarife[i]) for i, _ in r_items)
    rapor = report_data(api, me["user_id"], r_items, r_hours, r_tarife)
    o, r_bina, r_an = rapor["ozet"], rapor["binalar"], rapor["anomaliler"]
    st.caption(f"Dönem: {o['donem']} · {o['bina_sayisi']} bina")

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
        st.markdown('<div class="card-title">Bina bazlı özet</div>', unsafe_allow_html=True)
        if r_bina.empty:
            st.info("Bu dönem için veri yok.")
        else:
            st.dataframe(r_bina, use_container_width=True, hide_index=True)

    st.write("")
    with st.container(key="card_rapor_anomali"):
        st.markdown('<div class="card-title">Tasarruf fırsatları (anomaliler)</div>', unsafe_allow_html=True)
        if r_an.empty:
            st.success("Bu dönemde anomali yok.")
        else:
            st.dataframe(r_an.drop(columns=["Bitiş"]), use_container_width=True, hide_index=True)
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

raw = pd.DataFrame(api.readings(bid, 1440 if ozel else hours * 2))
forecast = pd.DataFrame(api.forecast(bid, 720 if ozel else hours))
anomalies = api.anomalies(bid, limit=50)

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
initials = "".join(w[0] for w in who.split()[:2]).upper() or "?"
aralik_txt = "-" if cur.empty else f"{tr_tarih(cur['ts'].min())} – {tr_tarih(cur['ts'].max())}"
h1, h3 = st.columns([1, 1.25], vertical_alignment="center")
with h1:
    st.markdown(
        f'<div class="hello">Merhaba, {html.escape(who)}</div>'
        '<div class="hello-sub">Enerji tüketim verilerini ve anomali durumlarını aşağıda görebilirsiniz.</div>'
        # Kullanıcı düğmesindeki avatar: baş harfler oturuma göre değiştiği için CSS'e burada yazılır.
        f"<style>.st-key-user_box button::before{{content:'{html.escape(initials)}';}}</style>",
        unsafe_allow_html=True,
    )
with h3:
    with st.container(horizontal=True, horizontal_alignment="right", gap="small", key="ust_sag"):
        # Tarih, bildirimler ve kullanıcı aynı satırda, sağa yaslı.
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
                    f'<div class="pop-title">Bildirimler</div><div class="pop-sub">Tüm binalarda son 24 saatte '
                    f'<b>{summary["active_alerts"]}</b> alarm · aşağıda {html.escape(names[bid])}</div>',
                    unsafe_allow_html=True,
                )
                if not anomalies:
                    st.caption("Bu bina için bildirim yok.")
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
        # Kullanıcı menüsü: profil özeti, Ayarlar ve Çıkış.
        with st.container(key="user_box"):
            with st.popover(who, help="Hesap"):
                st.markdown(
                    f'<div class="um-head"><div class="avatar">{html.escape(initials)}</div><div>'
                    f'<div class="um-name">{html.escape(who)}</div>'
                    f'<div class="um-mail">{html.escape(me["email"])}</div>'
                    f'<span class="um-role">{html.escape(me["role"])}</span></div></div>',
                    unsafe_allow_html=True,
                )
                st.button(
                    "Ayarlar", key="um_ayar", icon=":material/settings:", on_click=set_page, args=("ayar",),
                    use_container_width=True,
                )
                if st.button("Çıkış yap", key="um_cikis", icon=":material/logout:", use_container_width=True):
                    try:
                        api.logout()
                    finally:
                        st.session_state.clear()
                        st.rerun()

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
    kpi_card(svg("alert", 24), "red", "Tespit Edilen Anomali", str(n_an), f"<span>({donem}, bu bina)</span>"),
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
        st.caption(f"{len(rows)} bina · {donem}")
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
        st.markdown('<div class="card-title">Bina Bazlı Tüketim</div>', unsafe_allow_html=True)
        st.caption(f"En çok tüketen {min(len(rows), 5)} bina · {donem}")
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