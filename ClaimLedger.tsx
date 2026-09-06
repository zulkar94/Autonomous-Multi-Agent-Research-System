import type { ClaimOut } from "../lib/api";

interface Props {
  claims: ClaimOut[];
}

export function ClaimLedger({ claims }: Props) {
  if (claims.length === 0) return <p className="hint">No claim survived verification.</p>;

  return (
    <table>
      <thead>
        <tr>
          <th>Claim</th>
          <th>Sources</th>
          <th>Support</th>
        </tr>
      </thead>
      <tbody>
        {claims.map((claim) => (
          <tr key={claim.id} className={claim.status}>
            <td>
              {claim.text}
              {claim.rebuttals && (
                <div className="hint" style={{ fontSize: "0.82rem", marginTop: "0.3rem" }}>
                  Contested in {claim.rounds} round{claim.rounds === 1 ? "" : "s"}:{" "}
                  {claim.rebuttals.split("\n")[0]}
                </div>
              )}
            </td>
            <td>{claim.source_refs}</td>
            <td className="state">
              <div className="track">
                <div className="fill" style={{ width: `${Math.round(claim.support * 100)}%` }} />
              </div>
              {claim.status} {claim.support.toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
