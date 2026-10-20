import type { Building, Summary } from "../api";
import { buildingLabel, formatTl, formatTs } from "../format";
import { useLang } from "../i18n";

type Props = {
  summary?: Summary;
  buildings: Building[];
  onSelect: (id: number) => void;
};

export function Overview({ summary, buildings, onSelect }: Props) {
  const { t } = useLang();
  const worst = buildings.find((b) => b.building_id === summary?.worst_building_id);
  return (
    <section>
      <div className="cards">
        <Card label={t("overview.waste")} value={formatTl(summary?.total_cost_tl ?? null)} />
        <Card label={t("overview.alerts")} value={String(summary?.active_alerts ?? "-")} />
        <Card label={t("overview.worst")} value={worst ? buildingLabel(worst) : "-"} />
        <Card label={t("overview.buildings")} value={String(summary?.buildings ?? "-")} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("col.building")}</th>
              <th>{t("col.use")}</th>
              <th>{t("col.lastScored")}</th>
              <th>{t("col.anomalies")}</th>
            </tr>
          </thead>
          <tbody>
            {buildings.map((b) => (
              <tr key={b.building_id} className="clickable" onClick={() => onSelect(b.building_id)}>
                <td>
                  <button className="link">{buildingLabel(b)}</button>
                </td>
                <td>{b.segment ? t(`segment.${b.segment}` as Parameters<typeof t>[0]) : (b.primary_use ?? "-")}</td>
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
