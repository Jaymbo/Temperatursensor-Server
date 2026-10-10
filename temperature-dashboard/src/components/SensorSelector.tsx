import { useState } from "react";
import { BatteryBadge } from "./BatteryBadge";
import { latestSessionPerSensor, type BatterySummaryEntry } from "../utils";

interface Props {
  sensorSessions: { sensor_session: string; custom_text: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
  /** Akku-Status je Sensor (aus /battery/summary), fuer das Badge. */
  batterySummary?: BatterySummaryEntry[];
  /** Klick auf das Akku-Badge (oeffnet den Akku-Verlauf im Panel). */
  onBatteryClick?: (sensorId: string) => void;
}

export const SensorSelector = ({ sensorSessions, selected, onChange, batterySummary, onBatteryClick }: Props) => {
  const [searchTerm, setSearchTerm] = useState("");

  const filteredSessions = sensorSessions
    .filter((session) => {
    const sensorSession = session.sensor_session || ""; // format: sensorId_sessionId
    const customText = session.custom_text || "";
    // Display label format: #<sessionId> <customText>
    let displayLabel = sensorSession;
    const parts = sensorSession.split("_");
    if (parts.length === 2) {
      const sessionId = parts[1];
      displayLabel = `#${sessionId} ${customText || ''}`.trim();
    }
    const needle = searchTerm.toLowerCase();
    return (
      !sensorSession.startsWith("None_") &&
      (
        sensorSession.toLowerCase().includes(needle) ||
        customText.toLowerCase().includes(needle) ||
        displayLabel.toLowerCase().includes(needle)
      )
    );
    })
    .sort((a, b) => {
      const getId = (s: string) => {
        const parts = s.split("_");
        const id = parts[1];
        const num = id ? parseInt(id, 10) : NaN;
        return isNaN(num) ? -Infinity : num; // Nicht parsbare ans Ende
      };
      return getId(b.sensor_session) - getId(a.sensor_session); // absteigend
    });

  // Neueste Session je Sensor (fuer das Akku-Badge): nur dort erscheint das
  // Symbol, damit die Liste nicht zugeklappt wird.
  const latestSessions = latestSessionPerSensor(sensorSessions);
  const summaryBySensor = new Map(
    (batterySummary ?? []).map((e) => [String(e.sensor_id), e])
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <input
        type="text"
        placeholder="Search..."
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
        style={{ width: "100%", marginBottom: "0.5em", boxSizing: "border-box" }}
      />
      <select
        multiple
        style={{ width: "100%", flex: 1, minHeight: 0, boxSizing: "border-box", overflowY: "auto" }}
        onChange={(e) => {
          const values = Array.from(e.target.selectedOptions, (option) => option.value);
          onChange(values);
        }}
        value={selected}
      >
        {filteredSessions.map((session) => {
          const parts = session.sensor_session.split("_");
          let label = session.sensor_session;
          if (parts.length === 2) {
            const sessionId = parts[1];
            label = `#${sessionId} ${session.custom_text || ''}`.trim();
          }
          return (
            <option key={session.sensor_session} value={session.sensor_session} title={session.custom_text}>
              {label}
            </option>
          );
        })}
      </select>
      {/* Akku-Badges unter der Liste: je Sensor (neueste Session) ein klickbares
          Symbol + Spannung. Klicken oeffnet den Akku-Verlauf im Akku-Panel. */}
      {batterySummary && batterySummary.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3vh", paddingTop: "0.5vh" }}>
          {Object.entries(latestSessions).map(([sensorId, session]) => {
            const entry = summaryBySensor.get(sensorId);
            if (!entry) return null;
            return (
              <BatteryBadge
                key={session}
                entry={entry}
                compact
                onClick={onBatteryClick}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};
