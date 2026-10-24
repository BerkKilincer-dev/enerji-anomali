import html
from datetime import timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from api_client import Api, ApiError
from charts import build_events, consumption_chart, donut_chart, tr_tarih, tr_tarih_saat

st.set_page_config(page_title="Wattwise", page_icon="⚡", layout="wide")
css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

TARIFF_TL = 3.5  # VARSAYIM: kWh başına TL. Ekibin backend'deki tarifesiyle eşitleyin.


def num(x, d=0):
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def tl(x):
    return "-" if x is None else "₺ " + num(x)


def delta_html(cur, prev):
    if not prev:
        return "<span>Önceki dönem verisi yok</span>"
    p = (cur - prev) / prev * 100
    cls = "up" if p > 0 else "down"
    arrow = "↑" if p > 0 else "↓"
    return f'<span class="{cls}">{arrow} %{num(abs(p), 1)}</span> <span>önceki döneme göre</span>'


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
            if pct is not None:
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
                f'</div><span class="an-chev">›</span></div>'
            )
        body = "".join(rows)
    return (
        '<div class="an-panel"><div class="an-head"><span class="an-title">🔔 Son Anomaliler</span>'
        f'<span class="an-all">Tümünü Gör →</span></div><div class="an-list">{body}</div></div>'
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


@st.cache_data(ttl=600, show_spinner="Binalar yükleniyor...")
def kwh_by_building(_api, user_id, ids, hours):
    # user_id önbellek anahtarında: farklı kullanıcıların verisi birbirine karışmasın.
    out = {}
    for i in ids:
        try:
            rows = _api.readings(i, hours)
        except ApiError:
            continue
        out[i] = sum((r["kwh"] or 0) for r in rows)
    return out

# ---------- Giriş ----------
if "api" not in st.session_state:
    st.session_state.api = Api()
api = st.session_state.api

if "me" not in st.session_state:
    st.title("⚡ Wattwise")
    st.caption("Daha verimli bir yarın")
    with st.form("giris"):
        email = st.text_input("E-posta")
        sifre = st.text_input("Şifre", type="password")
        gonder = st.form_submit_button("Giriş yap")
    if gonder:
        try:
            st.session_state.me = api.login(email, sifre)
            st.rerun()
        except ApiError as e:
            st.error(f"Giriş başarısız: {e}")
        except Exception as e:
            st.error(f"API'ye ulaşılamadı: {e}")
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
ids = list(names)
en_cok = max(range(len(buildings)), key=lambda i: buildings[i]["anomaly_count"])

# ---------- Sol menü ----------
PAGES = [
    ("genel", "🏠", "Genel Bakış"),
    ("tuketim", "📊", "Tüketim Analizi"),
    ("anomali", "⚠️", "Anomaliler"),
    ("rapor", "📄", "Raporlar"),
    ("ayar", "⚙️", "Ayarlar"),
]
if "sayfa" not in st.session_state:
    st.session_state.sayfa = "genel"


def set_page(p):
    st.session_state.sayfa = p


with st.sidebar:
    st.markdown(
        '<div class="brand"><span class="bolt">⚡</span><div>'
        '<div class="brand-name">Wattwise</div>'
        '<div class="brand-sub">Daha verimli bir yarın</div></div></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="nav"):
        for pid, icon, ad in PAGES:
            st.button(
                f"{icon}  {ad}",
                key=f"nav_{pid}",
                use_container_width=True,
                type="primary" if st.session_state.sayfa == pid else "secondary",
                on_click=set_page,
                args=(pid,),
            )
    st.markdown(
        f"<style>.st-key-nav_anomali button p::after{{content:'{summary['active_alerts']}';}}</style>",
        unsafe_allow_html=True,
    )
    st.divider()
    bid = st.selectbox("Bina", ids, index=en_cok, format_func=lambda i: names[i], key="bina")
    if st.button("Çıkış"):
        try:
            api.logout()
        finally:
            st.session_state.clear()
            st.rerun()

if st.session_state.sayfa != "genel":
    st.info("Bu sayfa henüz hazır değil.")
    st.stop()

# ---------- Seçili aralık ve bina verisi ----------
# Gün/Hafta/Ay seçici grafik kartının içinde; değeri bir önceki çalıştırmadan okuyoruz.
aralik = st.session_state.get("aralik") or "Hafta"
hours = {"Gün": 24, "Hafta": 168, "Ay": 720}[aralik]
donem = {"Gün": "Son 24 saat", "Hafta": "Son 7 gün", "Ay": "Son 30 gün"}[aralik]

raw = pd.DataFrame(api.readings(bid, hours * 2))
forecast = pd.DataFrame(api.forecast(bid, hours))
anomalies = api.anomalies(bid, limit=50)

cutoff = None
cur = prev = raw
if not raw.empty:
    raw["ts"] = pd.to_datetime(raw["ts"], utc=True)
    cutoff = raw["ts"].max() - timedelta(hours=hours)
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
    win_anoms = [a for a in anomalies if pd.to_datetime(a["ts_start"], utc=True) > cutoff]
n_an = len(win_anoms)
tasarruf = sum((a["est_cost_tl"] or 0) for a in win_anoms)

# ---------- Üst bar ----------
initials = "".join(w[0] for w in who.split()[:2]).upper() or "?"
aralik_txt = "-" if cur.empty else f"{tr_tarih(cur['ts'].min())} – {tr_tarih(cur['ts'].max())}"
h1, h2, h3 = st.columns([5, 2.4, 2.2])
with h1:
    st.markdown(
        f'<div class="hello">Merhaba, {who}</div>'
        '<div class="hello-sub">Enerji tüketim verilerini ve anomali durumlarını aşağıda görebilirsiniz.</div>',
        unsafe_allow_html=True,
    )
with h2:
    st.markdown(f'<div class="daterange">📅 &nbsp;{aralik_txt}</div>', unsafe_allow_html=True)
with h3:
    st.markdown(
        f'<div class="chip"><div class="bell">🔔<b>{summary["active_alerts"]}</b></div>'
        f'<div class="avatar">{initials}</div>'
        f'<div class="who"><div class="n">{who}</div><div class="r">{me["role"]}</div></div></div>',
        unsafe_allow_html=True,
    )

# ---------- KPI kartları ----------
c1, c2, c3, c4 = st.columns(4)
c1.markdown(
    kpi_card("⚡", "blue", "Toplam Enerji Tüketimi", f"{num(kwh_cur)} kWh", delta_html(kwh_cur, kwh_prev)),
    unsafe_allow_html=True,
)
c2.markdown(
    kpi_card(
        "🪙", "orange", "Tahmini Maliyet", tl(kwh_cur * TARIFF_TL),
        delta_html(kwh_cur * TARIFF_TL, kwh_prev * TARIFF_TL),
    ),
    unsafe_allow_html=True,
)
c3.markdown(
    kpi_card("⚠️", "red", "Tespit Edilen Anomali", str(n_an), f"<span>({donem})</span>"),
    unsafe_allow_html=True,
)
c4.markdown(
    kpi_card("🍃", "green", "Tasarruf Potansiyeli", tl(tasarruf), f"<span>({donem}, anomali kaynaklı)</span>"),
    unsafe_allow_html=True,
)

st.write("")

# ---------- Ana grafik kartı + anomali paneli ----------
events = build_events(cur, forecast, win_anoms)
pct_by_id = {e["id"]: e["pct"] for e in events}

left, right = st.columns([2, 1])

with left:
    with st.container(key="chart_card"):
        t1, t2, t3 = st.columns([3, 2.2, 0.7])
        t1.markdown('<div class="card-title">Enerji Tüketim Grafiği</div>', unsafe_allow_html=True)
        with t2:
            st.segmented_control(
                "Aralık", ["Gün", "Hafta", "Ay"], default="Hafta", key="aralik", label_visibility="collapsed"
            )
        with t3:
            csv = cur[["ts", "kwh"]].to_csv(index=False).encode("utf-8") if not cur.empty else b""
            st.download_button("⬇", csv, file_name=f"tuketim_{bid}.csv", mime="text/csv", help="CSV olarak indir")

        st.markdown(
            '<div class="legend">'
            '<span><i class="dot" style="background:#2563EB"></i>Gerçekleşen Tüketim</span>'
            '<span><i class="ln"></i>Beklenen Tüketim</span>'
            '<span><i class="dot" style="background:#EF4444"></i>Anomali</span></div>',
            unsafe_allow_html=True,
        )
        if cur.empty:
            st.info("Bu aralıkta ölçüm yok.")
        else:
            fig = consumption_chart(cur, forecast, events, aralik)
            st.pyplot(fig, use_container_width=True)
            plt.close(fig)

with right:
    st.markdown(anomaly_panel(anomalies[:8], names[bid], pct_by_id), unsafe_allow_html=True)

# ---------- Alt satır: 3 kart ----------
PALET = ["#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#06B6D4", "#94A3B8"]
MAX_BINA = 20  # çok bina varsa ilk çağrıda yavaşlamasın diye üst sınır

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
SEV_RENK = {"Yüksek": "#EF4444", "Orta": "#F59E0B", "Düşük": "#10B981"}

b1, b2, b3 = st.columns(3)

with b1:
    with st.container(key="card_dagilim"):
        st.markdown('<div class="card-title">Tüketimin Dağılımı</div>', unsafe_allow_html=True)
        if toplam_kwh <= 0:
            st.info("Dağılım için veri yok.")
        else:
            d1, d2 = st.columns([1, 1.15])
            with d1:
                fig = donut_chart([v for _, v in dagilim], PALET[: len(dagilim)], num(toplam_kwh), "kWh")
                st.pyplot(fig, use_container_width=True)
                plt.close(fig)
            with d2:
                st.markdown(
                    legend_rows([(PALET[k], n, f"%{num(v / toplam_kwh * 100)}") for k, (n, v) in enumerate(dagilim)]),
                    unsafe_allow_html=True,
                )

with b2:
    with st.container(key="card_bina"):
        st.markdown('<div class="card-title">Bina Bazlı Tüketim</div>', unsafe_allow_html=True)
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
        d1, d2 = st.columns([1, 1.15])
        with d1:
            fig = donut_chart(
                [sev_say[k] for k in SEV_RENK], list(SEV_RENK.values()), str(toplam_uyari), "Toplam Uyarı"
            )
            st.pyplot(fig, use_container_width=True)
            plt.close(fig)
        with d2:
            st.markdown(
                legend_rows(
                    [
                        (SEV_RENK[k], k, f"{sev_say[k]} (%{num(sev_say[k] / toplam_uyari * 100) if toplam_uyari else 0})")
                        for k in SEV_RENK
                    ]
                ),
                unsafe_allow_html=True,
            )

st.write("")

st.markdown("#### Binalar")
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

# ---------- Alt durum çubuğu ----------
try:
    saglik = api.health()
except Exception:
    saglik = None

son_veri = "-" if cur.empty else tr_tarih_saat(cur["ts"].max())
if saglik:
    kaynak = {"demo": "demo verisi", "database": "veritabanı"}.get(saglik.get("mode"), saglik.get("mode"))
    durum = f'<span class="st-dot"></span>Sistem aktif · Veri kaynağı: {kaynak}'
else:
    durum = '<span class="st-dot off"></span>API yanıt vermiyor'
st.markdown(f'<div class="statusbar"><span>{durum}</span><span>Son veri: {son_veri}</span></div>', unsafe_allow_html=True)