import base64
from pathlib import Path
from urllib.parse import quote

PATHS = {
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "coins": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>',
    "bell": '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    "home": '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "file": '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "factory": '<path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M17 18h1"/><path d="M12 18h1"/><path d="M7 18h1"/>',
    "bulb": '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
}


def svg(name, size=20, color="currentColor", sw=2):
    return (
        # Simgeler süs; yanlarında her zaman metin var, ekran okuyucu atlasın.
        f'<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" '
        f'style="vertical-align:middle">{PATHS[name]}</svg>'
    )


# Wattwise logosu: brand/logo_160.png, kaynağı assets/logo.jpg (brand/make_logo.py ile şeffaf PNG'ye çevrilir).
# Menüde 38 px gösterilir; 160 px'lik sürüm yüksek çözünürlüklü ekranlarda da keskin kalır.
# Sekme simgesi ve sunumlar için 512 px'lik brand/logo.png kullanılır.
_LOGO_B64 = base64.b64encode((Path(__file__).parent / "brand" / "logo_160.png").read_bytes()).decode()
LOGO = (
    f'<img src="data:image/png;base64,{_LOGO_B64}" width="38" height="38" alt="" aria-hidden="true" '
    'style="display:block;flex:none">'
)

NAV_ICONS = {"genel": "home", "tuketim": "chart", "anomali": "alert", "rapor": "file", "ayar": "settings"}


def _data_uri(name, stroke="black"):
    s = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    )
    return "data:image/svg+xml;utf8," + quote(s)


def sidebar_css(alerts):
    """Menü ikonlarını çizgili SVG ile değiştirir, rozeti ve bina seçici ikonunu ayarlar."""
    css = []
    for pid, name in NAV_ICONS.items():
        u = _data_uri(name)
        css.append(
            f'.st-key-nav_{pid} button [data-testid="stIconMaterial"]{{font-size:0 !important;'
            "width:22px;height:22px;display:inline-block;background-color:currentColor;"
            f'-webkit-mask:url("{u}") center/contain no-repeat;mask:url("{u}") center/contain no-repeat;}}'
        )
    if alerts:
        css.append(f".st-key-nav_anomali button p::after{{content:'{alerts}';}}")
    else:
        css.append(".st-key-nav_anomali button p::after{display:none;}")
    f = _data_uri("factory", "#94A3B8")
    css.append(
        '.st-key-bina_box [data-baseweb="select"] > div{padding-left:40px;min-height:48px;'
        f'background-image:url("{f}");background-repeat:no-repeat;background-position:12px center;background-size:20px;}}'
    )
    return "".join(css)
