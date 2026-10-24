import { useEffect, useState } from "react";

import { api, ApiError, canWrite, type Me } from "./api";
import { Async } from "./components/Async";
import { BuildingDetail } from "./components/BuildingDetail";
import { Login } from "./components/Login";
import { Overview } from "./components/Overview";
import { type Lang, useLang } from "./i18n";
import { useAsync } from "./useAsync";

const selectedFromHash = () => Number(window.location.hash.match(/^#\/buildings\/(\d+)$/)?.[1]) || undefined;

export function App() {
  const { t, lang, setLang } = useLang();
  const health = useAsync(api.health, []);
  const me = useAsync(api.me, []);
  const signedOut = me.error instanceof ApiError && me.error.status === 401;

  async function signOut() {
    await api.logout().catch(() => undefined);
    me.reload();
  }

  return (
    <main>
      <header>
        <h1>{t("app.title")}</h1>
        {health.data?.mode === "demo" && <span className="tag">{t("app.demo")}</span>}
        <div className="header-end">
          <select aria-label="Dil / Language" value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
            <option value="tr">TR</option>
            <option value="en">EN</option>
          </select>
          {me.data && health.data?.mode === "database" && (
            <>
              <span className="muted">{me.data.email}</span>
              <button onClick={signOut}>{t("app.signOut")}</button>
            </>
          )}
        </div>
      </header>
      {signedOut ? (
        <Login onSignedIn={me.reload} />
      ) : (
        <Async state={me} errorPrefix={t("app.apiDown")}>
          {(user) => <Dashboard me={user} />}
        </Async>
      )}
    </main>
  );
}

function Dashboard({ me }: { me: Me }) {
  const { t } = useLang();
  const [selected, setSelected] = useState(selectedFromHash);
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

  return (
    <Async state={buildings} errorPrefix={t("app.apiDown")}>
      {(list) => {
        const building = list.find((b) => b.building_id === selected);
        return building ? (
          <BuildingDetail building={building} canWrite={canWrite(me)} onBack={() => select()} />
        ) : (
          <Overview summary={summary.data} buildings={list} onSelect={select} />
        );
      }}
    </Async>
  );
}
