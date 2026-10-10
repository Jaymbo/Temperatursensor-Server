import { levelColor, suspicionColor, relativeToMedian } from "../utils";
import type { FleetEntry } from "../hooks/useBatteryData";

/**
 * Flottenvergleich: normierte Balken der Entladerate je Sensor/Akku.
 *
 * Warum NORMiert? Ein absoluter Spannungswert kann "alt" nicht von "gerade
 * leer" unterscheiden (siehe backend/battery_health.py). Erst der Vergleich
 * ueber die Flotte macht eine Aussage moeglich. Die Balkenlaenge ist daher
 * der Betrag der Entladerate relativ zum Flotten-Median (Median = 100 %).
 */

interface Props {
  fleet: FleetEntry[];
  medianRate: number | null;
  onSelect?: (sensorId: string) => void;
}

export const FleetComparison = ({ fleet, medianRate, onSelect }: Props) => {
  if (fleet.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Flottendaten vorhanden.
      </div>
    );
  }

  const medianText =
    medianRate == null ? "unbekannt" : `${medianRate.toFixed(1)} mV/Tag`;

  return (
    <div>
      <div style={{ fontSize: "1.6vh", color: "#666", marginBottom: "0.8vh" }}>
        Flotten-Median der Entladerate: <strong>{medianText}</strong>
      </div>
      {fleet.map((e) => {
        const rel = relativeToMedian(e.rate_mv_per_day, medianRate);
        // Balkenbreite auf max. 100 % begrenzen (Ausreisser werden sonst unlesbar).
        const width = rel == null ? 0 : Math.min(100, rel);
        const color = suspicionColor(e.suspicion?.color);
        return (
          <div
            key={e.sensor_id}
            onClick={() => onSelect?.(e.sensor_id)}
            title={e.suspicion?.reason ?? ""}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.6vw",
              marginBottom: "0.5vh",
              cursor: onSelect ? "pointer" : "default",
            }}
          >
            <span style={{ width: "6vw", fontSize: "1.6vh", fontWeight: "bold" }}>
              #{e.sensor_id}
            </span>
            <span
              aria-hidden="true"
              style={{
                width: "1.2vh",
                height: "1.2vh",
                borderRadius: "50%",
                backgroundColor: levelColor(e.level?.stage),
                flex: "0 0 auto",
              }}
            />
            <div
              style={{
                flex: 1,
                height: "1.8vh",
                backgroundColor: "#eee",
                borderRadius: "0.9vh",
                overflow: "hidden",
              }}
            >
              <div
                data-testid={`fleet-bar-${e.sensor_id}`}
                style={{
                  width: `${width}%`,
                  height: "100%",
                  backgroundColor: color,
                }}
              />
            </div>
            <span style={{ width: "10vw", fontSize: "1.5vh", color: "#555" }}>
              {rel == null ? "—" : `${rel.toFixed(0)} %`}
              {e.rate_mv_per_day != null
                ? ` (${e.rate_mv_per_day.toFixed(1)} mV/Tag)`
                : ""}
            </span>
            <span style={{ width: "6vw", fontSize: "1.5vh", color: "#999" }}>
              {e.cycles != null ? `${e.cycles} Zyklen` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
};
