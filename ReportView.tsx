import type { RunDetail } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";
import { ClaimLedger } from "./ClaimLedger";

interface Props {
  run: RunDetail;
}

export function ReportView({ run }: Props) {
  if (run.status === "failed") {
    return <p className="notice">This run failed: {run.error ?? "unknown error"}</p>;
  }

  return (
    <div>
      <div className="readout">
        <div>
          <b>{Math.round(run.confidence * 100)}%</b>confidence
        </div>
        <div>
          <b>{Math.round(run.citation_coverage * 100)}%</b>citation coverage
        </div>
        <div>
          <b>{run.sources.length}</b>sources
        </div>
        <div>
          <b>{run.claims.length}</b>claims
        </div>
        <div>
          <b>{(run.duration_ms / 1000).toFixed(1)}s</b>elapsed
        </div>
      </div>

      <h3>Claims</h3>
      <ClaimLedger claims={run.claims} />

      <h3>Report</h3>
      <article
        className="report"
        // Markdown is rendered by a local escape-first converter: the source
        // text is escaped before any tag is emitted, so no raw HTML survives.
        dangerouslySetInnerHTML={{ __html: renderMarkdown(run.report_markdown ?? "") }}
      />
    </div>
  );
}
