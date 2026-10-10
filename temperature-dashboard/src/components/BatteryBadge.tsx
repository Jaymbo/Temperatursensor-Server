import { formatAge, levelColor, type BatterySummaryEntry } from "../utils";

/**
 * Akku-Badge: Symbol (Ampel) + Spannung + Stufe + Alter der letzten Messung.
 *
 * Zeigt BEWUSST eine Stufe statt Prozent (LiPo ist zwischen 3,7 und 3,9 V zu
 * flach fuer eine ehrliche Prozentangabe, siehe backend/battery_health.py).
 *
 * Kompakt-Variante (compact=true) fuer die Session-Liste: nur Akku-Symbol +
 * Spannung. Klicken oeffnet den Akku-Verlauf des Sensors im Akku-Panel.
 */

interface Props {
  entry: BatterySummaryEntry;
  onClick?: (sensorId: string) => void;
  compact?: boolean;
}

export const BatteryBadge = ({ entry, onClick, compact }: Props) => {
  const mv = entry.latest?.mv ?? null;
  const color = levelColor(entry.level?.stage);
  const underLoad = entry.level?.under_load;

  if (compact) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick?.(entry.sensor_id);
        }}
        title={
          entry.battery_id
            ? `Akku ${entry.battery_id}: ${mv != null ? `${(mv / 1000).toFixed(2)} V` : "—"} (${entry.level?.label ?? "unbekannt"}) – Klicken für Verlauf`
            : "Kein Akku zugeordnet"
        }
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "0.3vh",
          padding: "0.1vh 0.4vh",
          border: "none",
          borderRadius: "0.5vh",
          background: "transparent",
          cursor: onClick ? "pointer" : "default",
          fontSize: "inherit",
          color: "#222",
          lineHeight: 1,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: "1vh",
            height: "1vh",
            borderRadius: "50%",
            backgroundColor: color,
          }}
        />
        <span style={{ color: "#666" }}>
          {mv != null ? `${(mv / 1000).toFixed(2)} V` : "— V"}
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onClick?.(entry.sensor_id)}
      title={
        entry.battery_id
          ? `Akku ${entry.battery_id}`
          : "Kein Akku zugeordnet"
      }
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.5vw",
        padding: "0.4vh 0.6vw",
        border: `2px solid ${color}`,
        borderRadius: "1vh",
        backgroundColor: "#fff",
        cursor: onClick ? "pointer" : "default",
        fontSize: "1.6vh",
        color: "#222",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: "1.2vh",
          height: "1.2vh",
          borderRadius: "50%",
          backgroundColor: color,
        }}
      />
      <span style={{ fontWeight: "bold" }}>#{entry.sensor_id}</span>
      <span>
        {mv != null ? `${(mv / 1000).toFixed(2)} V` : "— V"}
      </span>
      <span style={{ color: "#666" }}>
        {entry.level?.label ?? "unbekannt"}
        {underLoad ? " (Last)" : ""}
      </span>
      <span style={{ color: "#999" }}>{formatAge(entry.age_seconds)}</span>
    </button>
  );
};
