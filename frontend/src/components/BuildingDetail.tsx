import { useState } from "react";

import { type Anomaly, api, type Building } from "../api";
import { buildingLabel } from "../format";
import { useAsync } from "../useAsync";
import { AnomalyTable } from "./AnomalyTable";
import { Async } from "./Async";
import { EnergyChart } from "./EnergyChart";

const RANGES = [
  { hours: 48, label: "2 days" },
  { hours: 24 * 7, label: "1 week" },
  { hours: 24 * 30, label: "30 days" },
];
const NO_ANOMALIES: Anomaly[] = [];

type Props = { building: Building; canWrite: boolean; onBack: () => void };

export function BuildingDetail({ building, canWrite, onBack }: Props) {
  const [hours, setHours] = useState(24 * 7);
  const id = building.building_id;
  const series = useAsync(() => Promise.all([api.readings(id, hours), api.forecast(id, hours)]), [id, hours]);
  const anomalies = useAsync(() => api.anomalies(id), [id]);

  return (
    <section>
      <button className="link" onClick={onBack}>
        ← All buildings
      </button>
      <h2>{buildingLabel(building)}</h2>
      <p className="muted">
        {[building.primary_use, building.timezone, building.country].filter(Boolean).join(" · ")}
      </p>

      <div className="toolbar">
        {RANGES.map((r) => (
          <button key={r.hours} className={r.hours === hours ? "active" : ""} onClick={() => setHours(r.hours)}>
            {r.label}
          </button>
        ))}
      </div>
      <Async state={series}>
        {([readings, forecast]) => (
          <EnergyChart readings={readings} forecast={forecast} anomalies={anomalies.data ?? NO_ANOMALIES} />
        )}
      </Async>

      <h3>Anomalies</h3>
      <Async state={anomalies}>
        {(list) => <AnomalyTable anomalies={list} canWrite={canWrite} onChange={anomalies.reload} />}
      </Async>
    </section>
  );
}
