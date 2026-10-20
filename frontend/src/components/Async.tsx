import type { ReactNode } from "react";

import type { AsyncState } from "../useAsync";

type Props<T> = { state: AsyncState<T>; errorPrefix?: string; children: (data: T) => ReactNode };

export function Async<T>({ state, errorPrefix = "", children }: Props<T>) {
  if (state.error) return <p className="error">{errorPrefix + state.error.message}</p>;
  if (state.data === undefined) return <p className="muted">Loading…</p>;
  return <>{children(state.data)}</>;
}
