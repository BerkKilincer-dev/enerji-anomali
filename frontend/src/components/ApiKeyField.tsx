import { useState } from "react";

type Props = { value: string; onChange: (key: string) => void };

export function ApiKeyField({ value, onChange }: Props) {
  const [draft, setDraft] = useState(value);
  return (
    <form
      className="api-key"
      onSubmit={(e) => {
        e.preventDefault();
        onChange(draft.trim());
      }}
    >
      <label>
        API key
        <input type="password" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="needed for feedback" autoComplete="off" />
      </label>
      <button type="submit">{value ? "Update" : "Save"}</button>
      {value && (
        <button type="button" onClick={() => (setDraft(""), onChange(""))}>
          Forget
        </button>
      )}
    </form>
  );
}
