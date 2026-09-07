import { useEffect, useRef } from "react";
import type { TraceEvent } from "../lib/api";

interface Props {
  events: TraceEvent[];
}

export function TraceLedger({ events }: Props) {
  const tail = useRef<HTMLDivElement>(null);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  return (
    <aside className="rail" aria-live="polite" aria-label="Agent trace">
      <h2>Agent trace</h2>
      {events.length === 0 ? (
        <p className="hint">Start a run to watch the agents plan, search, verify and debate.</p>
      ) : (
        events.map((event) => (
          <div className="trace-line" data-level={event.level} key={`${event.seq}-${event.agent}`}>
            <span className="agent">{event.agent}</span>
            <span>{event.message}</span>
          </div>
        ))
      )}
      <div ref={tail} />
    </aside>
  );
}
