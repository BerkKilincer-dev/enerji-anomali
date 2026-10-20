import { useCallback, useEffect, useState } from "react";

export type AsyncState<T> = { data?: T; error?: Error; reload: () => void };

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<{ data?: T; error?: Error }>({});
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    load()
      .then((data) => alive && setState({ data }))
      .catch((error: Error) => alive && setState({ error }));
    return () => {
      alive = false;
    };
  }, [...deps, version]);

  return { ...state, reload };
}
