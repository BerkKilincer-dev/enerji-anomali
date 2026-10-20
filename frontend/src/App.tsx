import { useEffect, useState } from "react";

import { api, getApiKey, setApiKey } from "./api";
import { ApiKeyField } from "./components/ApiKeyField";
import { Async } from "./components/Async";
import { BuildingDetail } from "./components/BuildingDetail";
import { Overview } from "./components/Overview";
import { useAsync } from "./useAsync";

const selectedFromHash = () => Number(window.location.hash.match(/^#\/buildings\/(\d+)$/)?.[1]) || undefined;

export function App() {
  const [selected, setSelected] = useState(selectedFromHash);
  const [apiKey, setKey] = useState(getApiKey);
  const health = useAsync(api.health, []);
  const summary = useAsync(api.summary, []);
  const buildings = useAsync(api.buildings, []);

  useEffect(() => {
    const sync = () => setSelected(selectedFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const select = (id?: number) => {
    window.location.hash = id ? `/buildings/${id}` : "";
  };
  const saveKey = (key: string) => {
    setApiKey(key);
    setKey(key);
  };
  const building = buildings.data?.find((b) => b.building_id === selected);

  return (
    <main>
      <header>
        <h1>Energy Anomaly Dashboard</h1>
        {health.data?.mode === "demo" && <span className="tag">demo data</span>}
        <ApiKeyField value={apiKey} onChange={saveKey} />
      </header>
      <Async state={buildings} errorPrefix="Cannot reach the API: ">
        {(list) =>
          building ? (
            <BuildingDetail building={building} canWrite={Boolean(apiKey)} onBack={() => select()} />
          ) : (
            <Overview summary={summary.data} buildings={list} onSelect={select} />
          )
        }
      </Async>
    </main>
  );
}
