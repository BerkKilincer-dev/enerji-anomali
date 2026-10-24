import { type FormEvent, useState } from "react";

import { api, ApiError } from "../api";
import { useLang } from "../i18n";

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const { t } = useLang();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await api.login(email.trim(), password);
      onSignedIn();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(status === 429 ? t("login.throttled") : status === 401 ? t("login.failed") : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel narrow" onSubmit={submit}>
      <h2>{t("login.title")}</h2>
      <label>
        {t("login.email")}
        <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>
        {t("login.password")}
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" className="primary" disabled={busy}>
        {t("login.submit")}
      </button>
    </form>
  );
}
