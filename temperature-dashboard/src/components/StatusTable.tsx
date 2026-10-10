import { formatAge, levelColor, suspicionColor } from "../utils";
import type { FleetEntry } from "../hooks/useBatteryData";

/**
 * Statusuebersicht je Sensor/Akku: Ampel, Spannung, Entladerate und
 * Verdachtsursache. Die Ursache trennt bewusst "Akku alt" von "Geraet defekt"
 * (siehe backend/battery_health.classify_suspicion).
 */

interface Props {
  entries: FleetEntry[];
  onSelect?: (sensorId: string) => void;
}

const formatRate = (rate: number | null): string => {
  if (rate == null || !Number.isFinite(rate)) return "—";
  // Rate ist negativ (Entladung); Betrag mit Vorzeichen ausweisen.
  return `${rate.toFixed(1)} mV/Tag`;
};

const formatDays = (days: number | null): string => {
  if (days == null || !Number.isFinite(days)) return "—";
  return `${days.toFixed(1)} d`;
};

export const StatusTable = ({ entries, onSelect }: Props) => {
  if (entries.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Akku-Daten vorhanden.
      </div>
    );
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "1.6vh" }}>
      <thead>
        <tr style={{ borderBottom: "2px solid #ddd", textAlign: "left" }}>
          <th style={{ padding: "0.5vh 0.4vw" }}>Sensor</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Akku</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Spannung</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Stufe</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Entladerate</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Resttage</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Zyklen</th>
          <th style={{ padding: "0.5vh 0.4vw" }}>Verdacht</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => {
          const color = levelColor(e.level?.stage);
          const sColor = suspicionColor(e.suspicion?.color);
          return (
            <tr
              key={e.sensor_id}
              onClick={() => onSelect?.(e.sensor_id)}
              style={{
                borderBottom: "1px solid #eee",
                cursor: onSelect ? "pointer" : "default",
              }}
            >
              <td style={{ padding: "0.5vh 0.4vw", fontWeight: "bold" }}>
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    width: "1.2vh",
                    height: "1.2vh",
                    borderRadius: "50%",
                    backgroundColor: color,
                    marginRight: "0.4vw",
                  }}
                />
                #{e.sensor_id}
              </td>
              <td style={{ padding: "0.5vh 0.4vw" }}>
                {e.battery_id ?? "—"}
              </td>
              <td style={{ padding: "0.5vh 0.4vw" }}>
                {e.latest_mv != null ? `${(e.latest_mv / 1000).toFixed(2)} V` : "—"}
              </td>
              <td style={{ padding: "0.5vh 0.4vw" }}>
                {e.level?.label ?? "unbekannt"}
                {e.level?.under_load ? " (Last)" : ""}
              </td>
              <td style={{ padding: "0.5vh 0.4vw" }}>{formatRate(e.rate_mv_per_day)}</td>
              <td style={{ padding: "0.5vh 0.4vw" }}>{formatDays(e.remaining_days)}</td>
              <td style={{ padding: "0.5vh 0.4vw" }}>{e.cycles ?? "—"}</td>
              <td style={{ padding: "0.5vh 0.4vw" }}>
                <span
                  title={e.suspicion?.reason ?? ""}
                  style={{
                    display: "inline-block",
                    padding: "0.2vh 0.4vw",
                    borderRadius: "0.6vh",
                    border: `1px solid ${sColor}`,
                    color: sColor,
                  }}
                >
                  {e.suspicion?.cause ?? "unbekannt"}
                </span>
                <span style={{ color: "#999", marginLeft: "0.4vw" }}>
                  {formatAge(
                    e.latest_at
                      ? (Date.now() - Date.parse(e.latest_at)) / 1000
                      : null
                  )}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};
