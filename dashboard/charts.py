import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

BLUE, RED, GRAY = "#2563EB", "#EF4444", "#64748B"
AY = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
GUN = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def tr_tarih(d):
    return f"{d.day} {AY[d.month - 1]} {d.year}"


def tr_tarih_saat(d):
    return f"{d.day} {AY[d.month - 1]} {d.year} {d:%H:%M}"


def _x_etiket(x, aralik):
    d = mdates.num2date(x)
    if aralik == "Gün":
        return f"{d:%H:%M}"
    s = f"{d.day} {AY[d.month - 1]}"
    return f"{s}\n{GUN[d.weekday()]}" if aralik == "Hafta" else s


def build_events(cur, forecast, anomalies):
    """Anomali kayıtlarını grafikte işaretlenecek olaylara çevirir."""
    events = []
    if cur.empty or not anomalies:
        return events
    exp = None
    if forecast is not None and not forecast.empty:
        exp = forecast.groupby("ts")["expected_kwh"].mean()
    valid = cur.dropna(subset=["kwh"])
    for a in anomalies:
        s = pd.to_datetime(a["ts_start"], utc=True)
        e = pd.to_datetime(a["ts_end"], utc=True)
        seg = valid[(valid["ts"] >= s) & (valid["ts"] <= e)]
        if seg.empty:
            continue
        peak = seg.loc[seg["kwh"].idxmax()]
        label, pct = None, None
        beklenen = exp.get(peak["ts"]) if exp is not None else None
        if beklenen and beklenen > 0:
            p = (peak["kwh"] - beklenen) / beklenen * 100
            if p > 0:
                pct = float(p)
                label = f"+%{p:.0f}"
        elif a.get("excess_kwh"):
            label = f"+{a['excess_kwh']:.0f} kWh"
        events.append(
            {"id": a["id"], "start": s, "end": e, "ts": peak["ts"], "kwh": peak["kwh"], "label": label, "pct": pct}
        )
    return events


def consumption_chart(cur, forecast, events, aralik):
    fig, ax = plt.subplots(figsize=(9, 3.9))
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")

    ymax = float(cur["kwh"].max())
    ax.plot(cur["ts"], cur["kwh"], color=BLUE, lw=2, zorder=3)
    ax.fill_between(cur["ts"], cur["kwh"], color=BLUE, alpha=0.08, zorder=2)

    if forecast is not None and not forecast.empty:
        ax.plot(forecast["ts"], forecast["expected_kwh"], color="#94A3B8", lw=1.6, ls=(0, (4, 3)), zorder=2)
        ymax = max(ymax, float(forecast["expected_kwh"].max()))

    minw = (cur["ts"].max() - cur["ts"].min()) / 50
    for ev in events:
        half = max((ev["end"] - ev["start"]) / 2, minw / 2)
        mid = ev["start"] + (ev["end"] - ev["start"]) / 2
        ax.axvspan(mid - half, mid + half, color="#FECACA", alpha=0.5, zorder=1, lw=0)
        ax.scatter([ev["ts"]], [ev["kwh"]], color=RED, s=45, zorder=5, edgecolors="white", linewidths=1.5)
        if ev["label"]:
            ax.annotate(
                ev["label"], (ev["ts"], ev["kwh"]), xytext=(0, 14), textcoords="offset points",
                ha="center", color="white", fontsize=9, fontweight="bold", zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", fc=RED, ec="none"),
            )

    ax.set_ylim(0, ymax * 1.2 if ymax > 0 else 1)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: _x_etiket(x, aralik)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", ".")))
    ax.text(0, 1.04, "kWh", transform=ax.transAxes, fontsize=9, color=GRAY)

    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#E2E8F0")
    ax.tick_params(colors=GRAY, labelsize=9, length=0)
    ax.grid(axis="y", color="#E2E8F0", lw=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return fig


def donut_chart(values, colors, center_top, center_bottom, size=2.2):
    fig, ax = plt.subplots(figsize=(size, size))
    fig.patch.set_alpha(0)
    if sum(values) <= 0:
        values, colors = [1], ["#E2E8F0"]
    ax.pie(
        values, colors=colors, startangle=90, counterclock=False,
        wedgeprops=dict(width=0.32, edgecolor="white", linewidth=2),
    )
    ax.text(0, 0.1, center_top, ha="center", va="center", fontsize=12, fontweight="bold", color="#0F172A")
    ax.text(0, -0.2, center_bottom, ha="center", va="center", fontsize=8, color=GRAY)
    ax.set(aspect="equal")
    fig.tight_layout(pad=0)
    return fig
