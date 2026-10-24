import base64
import html
import io
from datetime import timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import time

from datetime import datetime, timedelta, timezone
from reports import collect, to_csv, to_pdf, to_xlsx
from api_client import API_URL, Api, ApiError
from charts import build_events, consumption_chart, donut_chart, tr_tarih, tr_tarih_saat
from icons import LOGO, sidebar_css, svg

BRAND = "Wattwise"  # tasarım görselindeki ad: EnerjiVista
BRAND_SUB = "Daha verimli bir yarın"
TARIFF_TL = 3.5  # backend'in anomali açıklamalarındaki tarife (3,50 TL/kWh)

st.set_page_config(page_title=BRAND, page_icon="⚡", layout="wide")
css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def fig_html(fig, kind="donut"):
    """Matplotlib şeklini ortalanmış, gömülü bir görsele çevirir (Streamlit sarmalayıcılarından bağımsız)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'<div class="{kind}-wrap"><img src="data:image/png;base64,{b64}" alt=""></div>'


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
@st.cache_data(ttl=300, show_spinner="Rapor hazırlanıyor...")
def report_data(_api, user_id, items, hours, tariff):
    # user_id önbellek anahtarında: farklı kullanıcıların verisi karışmasın.
    return collect(_api, dict(items), hours, tariff)


# ---------- Giriş ----------
if "api" not in st.session_state:
    st.session_state.api = Api()
api = st.session_state.api

if "me" not in st.session_state:
    st.title(f"⚡ {BRAND}")
    st.caption(BRAND_SUB)
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

    def gonder(aid, verdict):
        try:
            api.feedback(aid, verdict)
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

        st.button("← Listeye dön", on_click=kapat_detay, key="detay_geri")
        st.markdown(
            '<div class="hello">Anomali Detayı</div>'
            f'<div class="hello-sub">{html.escape(names[bid])} · {tr_tarih_saat(s)} → {tr_tarih_saat(e)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{vb}</div>', unsafe_allow_html=True)

        try:
            ham = pd.DataFrame(api.readings(bid, 720))
            tah = pd.DataFrame(api.forecast(bid, 720))
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
        fark = f"%{(gercek / beklenen - 1) * 100:.0f} fazla" if gercek is not None and beklenen else "-"
        d1, d2, d3, d4 = st.columns(4)
        d1.markdown(kpi_card(svg("calendar", 24), "blue", "Süre", f"{sure:.0f} saat", "<span>(anomali penceresi)</span>"), unsafe_allow_html=True)
        d2.markdown(kpi_card(svg("zap", 24), "red", "Gerçekleşen", f"{num(gercek)} kWh" if gercek is not None else "-", f"<span>({fark})</span>"), unsafe_allow_html=True)
        d3.markdown(kpi_card(svg("chart", 24), "green", "Beklenen (baseline)", f"{num(beklenen)} kWh" if beklenen is not None else "-", "<span>(model tahmini)</span>"), unsafe_allow_html=True)
        d4.markdown(kpi_card(svg("coins", 24), "orange", "Fazla Tüketim", f"{num(a['excess_kwh'], 1)} kWh" if a["excess_kwh"] is not None else "-", f"<span>({tl(a['est_cost_tl'])})</span>"), unsafe_allow_html=True)
        st.write("")

        with st.container(key="card_detay_grafik"):
            st.markdown('<div class="card-title">Beklenen ve gerçekleşen tüketim</div>', unsafe_allow_html=True)
            if pencere.empty:
                st.info("Bu anomali son 30 günün dışında kaldığı için grafik gösterilemiyor.")
            else:
                ev = build_events(pencere, tah_p, [a])
                fig = consumption_chart(pencere, tah_p, ev, "Hafta")
                st.pyplot(fig, use_container_width=True)
                plt.close(fig)

        st.write("")
        with st.container(key="card_detay_bilgi"):
            st.markdown('<div class="card-title">Açıklama ve öneri</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="an-desc">{html.escape(a["explanation"] or "-")}</div>'
                + (f'<div class="an-advice">💡 {html.escape(a["advice"])}</div>' if a["advice"] else ""),
                unsafe_allow_html=True,
            )
            if yazabilir:
                b1, b2, b3, _ = st.columns([1, 1.3, 1, 3])
                b1.button("✅ Onayla", key=f"d_ok_{a['id']}", on_click=gonder, args=(a["id"], "confirmed"))
                b2.button("🚫 Yanlış alarm", key=f"d_fa_{a['id']}", on_click=gonder, args=(a["id"], "false_alarm"))
                b3.button("📅 Planlı", key=f"d_pl_{a['id']}", on_click=gonder, args=(a["id"], "planned"))
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
        tavsiye = f'<div class="an-advice">💡 {html.escape(a["advice"])}</div>' if a["advice"] else ""
        with st.container(key=f"anom_{a['id']}"):
            st.markdown(
                f'<div class="an-row-top"><span class="badge sev-{ton}">{ad}</span>{vb}'
                f'<span class="an-time">{bas} → {bit}</span></div>'
                f'<div class="an-desc">{html.escape(a["explanation"] or "-")}</div>'
                f'<div class="an-cost">Fazla tüketim: <b>{fazla}</b> · '
                f'Tahmini maliyet etkisi: <b>{tl(a["est_cost_tl"])}</b></div>{tavsiye}',
                unsafe_allow_html=True,
            )
            b0, b1, b2, b3, _ = st.columns([1, 1, 1.3, 1, 2])
            b0.button("🔍 Detay", key=f"dt_{a['id']}", on_click=ac_detay, args=(a["id"],))
            if yazabilir:
                b1.button("✅ Onayla", key=f"ok_{a['id']}", on_click=gonder, args=(a["id"], "confirmed"))
                b2.button("🚫 Yanlış alarm", key=f"fa_{a['id']}", on_click=gonder, args=(a["id"], "false_alarm"))
                b3.button("📅 Planlı", key=f"pl_{a['id']}", on_click=gonder, args=(a["id"], "planned"))
    st.stop()

# ---------- Ayarlar sayfası ----------
if st.session_state.sayfa == "ayar":
    st.markdown(
        '<div class="hello">Ayarlar</div><div class="hello-sub">Hesap ve bağlantı bilgileri</div>',
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
            f'<div class="set-row"><span>Tarife (varsayım)</span><span>{num(TARIFF_TL, 2)} TL/kWh</span></div>',
            unsafe_allow_html=True,
        )
    st.stop()

# ---------- Tüketim Analizi sayfası (şimdilik bina listesi) ----------
if st.session_state.sayfa == "tuketim":
    st.markdown(
        '<div class="hello">Tüketim Analizi</div><div class="hello-sub">Tüm binalar</div>',
        unsafe_allow_html=True,
    )
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
        '<div class="hello">Raporlar</div>'
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
    rapor = report_data(api, me["user_id"], r_items, r_hours, TARIFF_TL)
    o, r_bina, r_an = rapor["ozet"], rapor["binalar"], rapor["anomaliler"]
    st.caption(f"Dönem: {o['donem']} · {o['bina_sayisi']} bina")

    st.write("")
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(
        kpi_card(svg("zap", 24), "blue", "Toplam Enerji Tüketimi", f"{num(o['kwh'])} kWh", delta_html(o["kwh"], o["kwh_onceki"], "önceki döneme göre")),
        unsafe_allow_html=True,
    )
    k2.markdown(
        kpi_card(svg("coins", 24), "orange", "Tahmini Maliyet", tl(o["maliyet"]), f"<span>({num(o['tarife'], 2)} TL/kWh varsayımı)</span>"),
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

aralik = st.session_state.get("aralik") or "Hafta"
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

# ---------- Üst bar ----------
initials = "".join(w[0] for w in who.split()[:2]).upper() or "?"
aralik_txt = "-" if cur.empty else f"{tr_tarih(cur['ts'].min())} – {tr_tarih(cur['ts'].max())}"
h1, h2, h3 = st.columns([5, 2.6, 2.4])
with h1:
    st.markdown(
        f'<div class="hello">Merhaba, {html.escape(who)}</div>'
        '<div class="hello-sub">Enerji tüketim verilerini ve anomali durumlarını aşağıda görebilirsiniz.</div>',
        unsafe_allow_html=True,
    )
with h2:
    with st.container(key="tarih_box"):
        with st.popover(aralik_txt, icon=":material/calendar_month:", use_container_width=True):
            if son is None:
                st.caption("Veri yok")
            else:
                st.date_input(
                    "Tarih aralığı",
                    value=((son - timedelta(days=6)).date(), son.date()),
                    min_value=(son - timedelta(days=59)).date(),
                    max_value=son.date(),
                    format="DD.MM.YYYY",
                    key="ozel_aralik",
                    on_change=lambda: st.session_state.update(aralik="Özel"),
                )
with h3:
    st.markdown(
        f'<div class="chip"><div class="bell">{svg("bell", 20)}<b>{summary["active_alerts"]}</b></div>'
        f'<div class="avatar">{initials}</div>'
        f'<div class="who"><div class="n">{html.escape(who)}</div><div class="r">{me["role"]}</div></div>'
        f'{svg("chevron-down", 16, "#64748B")}</div>',
        unsafe_allow_html=True,
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
        svg("coins", 24), "orange", "Tahmini Maliyet", tl(kwh_cur * TARIFF_TL),
        delta_html(kwh_cur * TARIFF_TL, kwh_prev * TARIFF_TL, karsilas),
    ),
    unsafe_allow_html=True,
)
c3.markdown(
    kpi_card(svg("alert", 24), "red", "Tespit Edilen Anomali", str(n_an), f"<span>({donem})</span>"),
    unsafe_allow_html=True,
)
c4.markdown(
    kpi_card(svg("leaf", 24), "green", "Tasarruf Potansiyeli", tl(tasarruf), f"<span>({donem}, anomali kaynaklı)</span>"),
    unsafe_allow_html=True,
)

st.write("")

# ---------- Ana grafik kartı + anomali paneli ----------
events = build_events(cur, forecast, win_anoms)
pct_by_id = {e["id"]: e["pct"] for e in events}

left, right = st.columns([2, 1])

with left:
    with st.container(key="chart_card"):
        t1, t2, t3 = st.columns([2.4, 2.9, 0.7])
        t1.markdown('<div class="card-title">Enerji Tüketim Grafiği</div>', unsafe_allow_html=True)
        with t2:
            st.segmented_control(
    "Aralık", ["Gün", "Hafta", "Ay", "Özel"], default="Hafta", key="aralik", label_visibility="collapsed"
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
SEV_RENK = {"Yüksek": "#EF4444", "Orta": "#F59E0B", "Düşük": "#10B981"}

st.write("")
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
                st.markdown(fig_html(fig), unsafe_allow_html=True)
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
            st.markdown(fig_html(fig), unsafe_allow_html=True)
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

# ---------- Alt durum çubuğu ----------
try:
    saglik = api.health()
except Exception:
    saglik = None

son_veri = "-" if cur.empty else tr_tarih_saat(cur["ts"].max())
if saglik:
    kaynak = {"demo": "demo verisi", "database": "veritabanı"}.get(saglik.get("mode"), saglik.get("mode"))
    durum = f'<span class="st-dot"></span>Sistem aktif · Son güncelleme: {son_veri}'
    sag = f"Veri kaynağı: {kaynak}"
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