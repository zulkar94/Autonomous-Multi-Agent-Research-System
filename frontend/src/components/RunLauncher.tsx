import { useState } from "react";

interface Props {
  running: boolean;
  onStart: (query: string, depth: number) => void;
  onCancel: () => void;
}

const DEPTHS = [
  { value: 1, label: "1 — quick scan, no debate" },
  { value: 2, label: "2 — standard, two debate rounds" },
  { value: 3, label: "3 — thorough, adversarial review" },
];

export function RunLauncher({ running, onStart, onCancel }: Props) {
  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState(2);

  return (
    <div>
      <h2>Ask a research question</h2>
      <label htmlFor="query">Question</label>
      <textarea
        id="query"
        rows={3}
        placeholder="Does structured code review reduce production defects?"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <label htmlFor="depth">Depth</label>
      <select
        id="depth"
        value={depth}
        onChange={(event) => setDepth(Number(event.target.value))}
      >
        {DEPTHS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <div className="actions">
        <button disabled={running || query.trim().length < 8} onClick={() => onStart(query, depth)}>
          Start research
        </button>
        <button className="ghost" disabled={!running} onClick={onCancel}>
          Cancel run
        </button>
      </div>
    </div>
  );
}
