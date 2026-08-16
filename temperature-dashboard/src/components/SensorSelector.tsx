import { useState } from "react";

interface Props {
  sensorSessions: { sensor_session: string; custom_text: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export const SensorSelector = ({ sensorSessions, selected, onChange }: Props) => {
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
    </div>
  );
};
