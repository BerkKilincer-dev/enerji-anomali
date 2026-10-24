import { lazy, Suspense, useState } from "react";

import { type Anomaly, api, type Building } from "../api";
import { buildingLabel } from "../format";
import { useLang } from "../i18n";
import { useAsync } from "../useAsync";
import { AnomalyTable } from "./AnomalyTable";
import { Async } from "./Async";

// ECharts is most of the bundle; it loads only when a building is opened.
const EnergyChart = lazy(() => import("./EnergyChart"));

const RANGES = [
  { hours: 48, label: "range.2d" },
  { hours: 24 * 7, label: "range.1w" },
  { hours: 24 * 30, label: "range.30d" },
] as const;
const NO_ANOMALIES: Anomaly[] = [];

type Props = { building: Building; canWrite: boolean; onBack: () => void };

export function BuildingDetail({ building, canWrite, onBack }: Props) {
  const { t } = useLang();
  const [hours, setHours] = useState(24 * 7);
  const id = building.building_id;
  const series = useAsync(() => Promise.all([api.readings(id, hours), api.forecast(id, hours)]), [id, hours]);
  const anomalies = useAsync(() => api.anomalies(id), [id]);

  return (
    <section>
      <button className="link" onClick={onBack}>
        {t("detail.back")}
      </button>
      <h2>{buildingLabel(building)}</h2>
      <p className="muted">
        {[building.segment && t(`segment.${building.segment}` as Parameters<typeof t>[0]), building.timezone, building.country]
          .filter(Boolean)
          .join(" · ")}
      </p>

      <div className="toolbar">
        {RANGES.map((r) => (
          <button key={r.hours} className={r.hours === hours ? "active" : ""} onClick={() => setHours(r.hours)}>
            {t(r.label)}
          </button>
        ))}
      </div>
      <Async state={series}>
        {([readings, forecast]) => (
          <Suspense fallback={<div className="chart" />}>
            <EnergyChart readings={readings} forecast={forecast} anomalies={anomalies.data ?? NO_ANOMALIES} />
          </Suspense>
        )}
      </Async>

      <h3>{t("detail.anomalies")}</h3>
      <Async state={anomalies}>
        {(list) => <AnomalyTable anomalies={list} canWrite={canWrite} onChange={anomalies.reload} />}
      </Async>
    </section>
  );
}
