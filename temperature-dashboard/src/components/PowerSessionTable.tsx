import { type PowerSessionSummary } from "../hooks/usePowerData";

/**
 * Firmware-Versionsvergleich: Verbrauch pro Messzyklus je Session.
 *
 * Jede Temperatursensor-Session entspricht einer Firmware-Version (neue
 * Session = neue Version, siehe /update/{sensor_id}). Die Tabelle zeigt pro
 * Session den Gesamt-/Median-Verbrauch pro Zyklus (charge_uah) und die
 * Median-Dauer - so lassen sich zwei Versionen direkt vergleichen.
 */

interface Props {
  sessions: PowerSessionSummary[];
}

const fmt = (v: number | null, unit: string, digits = 2) =>
  v === null ? "-" : `${v.toFixed(digits)} ${unit}`;

export const PowerSessionTable = ({ sessions }: Props) => {
  if (sessions.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Session-Zuordnung (noch keine Temperatursession angelegt oder
        keine vollstaendigen Zyklen).
      </div>
    );
  }

  return (
    <table style={{ borderCollapse: "collapse", fontSize: "1.8vh" }}>
      <thead>
        <tr>
          <th style={{ textAlign: "left", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Session</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Zyklen</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Q gesamt (µAh)</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Q/Zyklus Median (µAh)</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Dauer Median (s)</th>
          <th style={{ textAlign: "right", padding: "0.4vh 1vw", borderBottom: "1px solid #ccc" }}>Peak Median (mA)</th>
        </tr>
      </thead>
      <tbody>
        {sessions.map((s) => (
          <tr key={s.session_id ?? "none"}>
            <td style={{ padding: "0.4vh 1vw" }}>
              {s.session_id === null ? "ohne Session" : `#${s.session_id}`}
            </td>
            <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>
              {s.n_cycles_complete}/{s.n_cycles}
            </td>
            <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{fmt(s.charge_uah_total, "µAh", 1)}</td>
            <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{fmt(s.charge_uah_median, "µAh")}</td>
            <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{fmt(s.duration_median_s, "s", 3)}</td>
            <td style={{ textAlign: "right", padding: "0.4vh 1vw" }}>{fmt(s.peak_ma_median, "mA")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};
