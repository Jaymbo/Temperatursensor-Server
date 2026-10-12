import { POWER_PHASE_ORDER, type PowerPhaseStat } from "../hooks/usePowerData";
import { PHASE_LABEL } from "./PowerOverlayChart";

/**
 * Phasen-Dauern je Zyklus (min/median/max) aus der Zyklusanalyse.
 * Zeigt z. B., dass die WLAN-Phase mal 0,8 s und mal 2,3 s dauert, wenn der
 * Server erst spaeter antwortet.
 */

interface Props {
  stats: Record<string, PowerPhaseStat>;
}

export const PowerPhaseTable = ({ stats }: Props) => {
  const rows = POWER_PHASE_ORDER.filter((p) => stats[p]);

  if (rows.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Phasen-Statistik (zu wenige vollstaendige Zyklen).
      </div>
    );
  }

  return (
    <table style={{ borderCollapse: "collapse", fontSize: "1.8vh" }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Phase</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>n</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>min (s)</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Median (s)</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>max (s)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => {
          const s = stats[p];
          return (
            <tr key={p}>
              <td style={{ padding: "0.4vh 1vw" }}>{PHASE_LABEL[p] ?? p}</td>
              <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{s.n}</td>
              <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{s.min_s.toFixed(3)}</td>
              <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{s.median_s.toFixed(3)}</td>
              <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{s.max_s.toFixed(3)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};
