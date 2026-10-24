// Turkish first, English as a switch; the choice is remembered per browser.
import { createContext, type ReactNode, useContext, useState } from "react";

export type Lang = "tr" | "en";
const LANG_STORAGE = "energy-anomaly.lang";

const tr = {
  "app.title": "Enerji Anomali CRM",
  "app.demo": "demo verisi",
  "app.apiDown": "API'ye ulaşılamıyor: ",
  "app.loading": "Yükleniyor…",
  "app.signOut": "Çıkış",
  "login.title": "Giriş yap",
  "login.email": "E-posta",
  "login.password": "Şifre",
  "login.submit": "Giriş",
  "login.failed": "E-posta veya şifre hatalı.",
  "login.throttled": "Çok fazla hatalı deneme. Biraz sonra tekrar deneyin.",
  "overview.waste": "Tahmini israf",
  "overview.alerts": "Son 24 saatteki alarmlar",
  "overview.worst": "En çok israf",
  "overview.buildings": "Sayaçlar",
  "col.building": "Sayaç",
  "col.use": "İşletme türü",
  "col.lastScored": "Son skorlama",
  "col.anomalies": "Anomaliler",
  "col.window": "Zaman aralığı",
  "col.type": "Tür",
  "col.excess": "Fazla tüketim",
  "col.cost": "Maliyet",
  "col.explanation": "Açıklama",
  "col.verdict": "Değerlendirme",
  "detail.back": "← Tüm sayaçlar",
  "detail.anomalies": "Anomaliler",
  "detail.none": "Bu sayaç için anomali yok.",
  "range.2d": "2 gün",
  "range.1w": "1 hafta",
  "range.30d": "30 gün",
  "chart.actual": "Gerçek",
  "chart.expected": "Beklenen",
  "chart.label": "Gerçek ve beklenen tüketim",
  "verdict.confirmed": "Doğru alarm",
  "verdict.false_alarm": "Yanlış alarm",
  "verdict.planned": "Planlı",
  "verdict.denied": "Bu işlem için yetkiniz yok.",
  "advice.title": "Öneri",
  "rule.night_base_load": "Gece yükü",
  "rule.weekend_usage": "Hafta sonu yükü",
  "rule.spike": "Ani sıçrama",
  "rule.dropout": "Veri kesintisi",
  "rule.flatline": "Takılı sayaç",
  "rule.rule": "Kural",
  "rule.model": "Model",
  "segment.office": "Ofis",
  "segment.mall": "AVM",
  "segment.retail": "Mağaza",
  "segment.hotel": "Otel",
  "segment.hospital": "Hastane",
  "segment.education": "Eğitim",
  "segment.factory": "Fabrika",
  "segment.warehouse": "Depo",
  "segment.data_center": "Veri merkezi",
  "segment.residential": "Konut",
  "segment.public": "Kamu",
  "segment.other": "Diğer",
};

type Key = keyof typeof tr;

const en: Record<Key, string> = {
  "app.title": "Energy Anomaly CRM",
  "app.demo": "demo data",
  "app.apiDown": "Cannot reach the API: ",
  "app.loading": "Loading…",
  "app.signOut": "Sign out",
  "login.title": "Sign in",
  "login.email": "E-mail",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.failed": "Wrong e-mail or password.",
  "login.throttled": "Too many failed attempts. Try again later.",
  "overview.waste": "Estimated waste",
  "overview.alerts": "Alerts in the last 24 h",
  "overview.worst": "Highest waste",
  "overview.buildings": "Meters",
  "col.building": "Meter",
  "col.use": "Business type",
  "col.lastScored": "Last scored",
  "col.anomalies": "Anomalies",
  "col.window": "Window",
  "col.type": "Type",
  "col.excess": "Excess",
  "col.cost": "Cost",
  "col.explanation": "Explanation",
  "col.verdict": "Verdict",
  "detail.back": "← All meters",
  "detail.anomalies": "Anomalies",
  "detail.none": "No anomalies for this meter.",
  "range.2d": "2 days",
  "range.1w": "1 week",
  "range.30d": "30 days",
  "chart.actual": "Actual",
  "chart.expected": "Expected",
  "chart.label": "Actual and expected consumption",
  "verdict.confirmed": "Confirmed",
  "verdict.false_alarm": "False alarm",
  "verdict.planned": "Planned",
  "verdict.denied": "You are not allowed to do this.",
  "advice.title": "Advice",
  "rule.night_base_load": "Night load",
  "rule.weekend_usage": "Weekend load",
  "rule.spike": "Spike",
  "rule.dropout": "Dropout",
  "rule.flatline": "Stuck meter",
  "rule.rule": "Rule",
  "rule.model": "Model",
  "segment.office": "Office",
  "segment.mall": "Mall",
  "segment.retail": "Retail",
  "segment.hotel": "Hotel",
  "segment.hospital": "Hospital",
  "segment.education": "Education",
  "segment.factory": "Factory",
  "segment.warehouse": "Warehouse",
  "segment.data_center": "Data center",
  "segment.residential": "Residential",
  "segment.public": "Public",
  "segment.other": "Other",
};

const DICTIONARIES: Record<Lang, Record<Key, string>> = { tr, en };

export type T = (key: Key) => string;

function storedLang(): Lang {
  try {
    return localStorage.getItem(LANG_STORAGE) === "en" ? "en" : "tr";
  } catch {
    return "tr";
  }
}

const LangContext = createContext<{ lang: Lang; setLang: (lang: Lang) => void; t: T }>({
  lang: "tr",
  setLang: () => {},
  t: (key) => tr[key],
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setState] = useState<Lang>(storedLang);
  const setLang = (next: Lang) => {
    setState(next);
    document.documentElement.lang = next;
    try {
      localStorage.setItem(LANG_STORAGE, next);
    } catch {
      // Private windows may refuse storage; the choice then lasts for this page only.
    }
  };
  const t: T = (key) => DICTIONARIES[lang][key];
  return <LangContext.Provider value={{ lang, setLang, t }}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext);
