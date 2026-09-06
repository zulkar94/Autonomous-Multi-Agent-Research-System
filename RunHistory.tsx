import type { RunSummary } from "../lib/api";

interface Props {
  runs: RunSummary[];
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

export function RunHistory({ runs, onOpen, onDelete }: Props) {
  if (runs.length === 0) return <p className="hint">Nothing here yet.</p>;

  return (
    <ul className="history">
      {runs.map((run) => (
        <li key={run.id}>
          <button onClick={() => onOpen(run.id)}>{run.query.slice(0, 90)}</button>
          <span className="state">{run.status}</span>
          <button className="ghost" onClick={() => onDelete(run.id)} aria-label="Delete run">
            Delete
          </button>
        </li>
      ))}
    </ul>
  );
}
