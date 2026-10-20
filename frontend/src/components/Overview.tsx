import type { Building, Summary } from "../api";
import { buildingLabel, formatTl, formatTs } from "../format";

type Props = { summary?: Summary; buildings: Building[]; onSelect: (id: number) => void };

export function Overview({ summary, buildings, onSelect }: Props) {
  const worst = buildings.find((b) => b.building_id === summary?.worst_building_id);
  return (
    <section>
      <div className="cards">
        <Card label="Estimated waste" value={formatTl(summary?.total_cost_tl ?? null)} />
        <Card label="Alerts in the last 24 h" value={String(summary?.active_alerts ?? "-")} />
        <Card label="Highest waste" value={worst ? buildingLabel(worst) : "-"} />
        <Card label="Buildings" value={String(summary?.buildings ?? "-")} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Building</th>
              <th>Use</th>
              <th>Last scored</th>
              <th>Anomalies</th>
            </tr>
          </thead>
          <tbody>
            {buildings.map((b) => (
              <tr key={b.building_id} className="clickable" onClick={() => onSelect(b.building_id)}>
                <td>
                  <button className="link">{buildingLabel(b)}</button>
                </td>
                <td>{b.primary_use ?? "-"}</td>
                <td className="nowrap">{formatTs(b.last_scored_ts)}</td>
                <td>{b.anomaly_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="muted">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}
