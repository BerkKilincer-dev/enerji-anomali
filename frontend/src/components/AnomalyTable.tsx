import { useState } from "react";

import { api, ApiError, type Anomaly, type Verdict } from "../api";
import { anomalyLabel, formatKwh, formatTl, formatTs, hourAfter } from "../format";

const VERDICTS: { value: Verdict; label: string }[] = [
  { value: "confirmed", label: "Confirm" },
  { value: "false_alarm", label: "False alarm" },
  { value: "planned", label: "Planned" },
];

type Props = { anomalies: Anomaly[]; canWrite: boolean; onChange: () => void };

export function AnomalyTable({ anomalies, canWrite, onChange }: Props) {
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<number>();

  async function send(id: number, verdict: Verdict) {
    setBusy(id);
    setError(undefined);
    try {
      await api.feedback(id, verdict);
      onChange();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? "API key is missing or wrong." : (e as Error).message);
    } finally {
      setBusy(undefined);
    }
  }

  if (anomalies.length === 0) return <p className="muted">No anomalies for this building.</p>;
  return (
    <>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Window</th>
              <th>Type</th>
              <th>Excess</th>
              <th>Cost</th>
              <th>Explanation</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {anomalies.map((a) => (
              <tr key={a.id}>
                <td className="nowrap">
                  {formatTs(a.ts_start)}
                  <br />
                  {formatTs(hourAfter(a.ts_end))}
                </td>
                <td>{anomalyLabel(a.source, a.rule_type)}</td>
                <td>{formatKwh(a.excess_kwh)}</td>
                <td>{formatTl(a.est_cost_tl)}</td>
                <td className="explanation">{a.explanation ?? "-"}</td>
                <td>
                  {a.verdict ? (
                    <span className={`tag ${a.verdict}`}>{a.verdict.replace("_", " ")}</span>
                  ) : canWrite ? (
                    VERDICTS.map((v) => (
                      <button key={v.value} disabled={busy === a.id} onClick={() => send(a.id, v.value)}>
                        {v.label}
                      </button>
                    ))
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
