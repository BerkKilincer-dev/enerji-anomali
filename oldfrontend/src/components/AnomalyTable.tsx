import { useState } from "react";

import { api, ApiError, type Anomaly, type Verdict } from "../api";
import { anomalyLabel, formatKwh, formatTl, formatTs, hourAfter } from "../format";
import { useLang } from "../i18n";

const VERDICTS: Verdict[] = ["confirmed", "false_alarm", "planned"];

type Props = { anomalies: Anomaly[]; canWrite: boolean; onChange: () => void };

export function AnomalyTable({ anomalies, canWrite, onChange }: Props) {
  const { t } = useLang();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<number>();

  async function send(id: number, verdict: Verdict) {
    setBusy(id);
    setError(undefined);
    try {
      await api.feedback(id, verdict);
      onChange();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 403 ? t("verdict.denied") : (e as Error).message);
    } finally {
      setBusy(undefined);
    }
  }

  if (anomalies.length === 0) return <p className="muted">{t("detail.none")}</p>;
  return (
    <>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("col.window")}</th>
              <th>{t("col.type")}</th>
              <th>{t("col.excess")}</th>
              <th>{t("col.cost")}</th>
              <th>{t("col.explanation")}</th>
              <th>{t("col.verdict")}</th>
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
                <td>{anomalyLabel(t, a.source, a.rule_type)}</td>
                <td>{formatKwh(a.excess_kwh)}</td>
                <td>{formatTl(a.est_cost_tl)}</td>
                <td className="explanation">
                  {a.explanation ?? "-"}
                  {a.advice && (
                    <p className="advice">
                      <strong>{t("advice.title")}:</strong> {a.advice}
                    </p>
                  )}
                </td>
                <td>
                  {a.verdict ? (
                    <span className={`tag ${a.verdict}`}>{t(`verdict.${a.verdict}`)}</span>
                  ) : canWrite ? (
                    VERDICTS.map((v) => (
                      <button key={v} disabled={busy === a.id} onClick={() => send(a.id, v)}>
                        {t(`verdict.${v}`)}
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
