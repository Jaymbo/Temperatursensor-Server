import { useEffect, useState } from "react";
import { Chart } from "./components/Chart";
import { SensorSelector } from "./components/SensorSelector";
import { useSensorData } from "./hooks/useSensorData";
import { useWebSocket } from "./hooks/useWebSocket";
import { buildApiUrl, getApiBase } from "./api";
import { parseCorrectionPoints, type CorrectionPoint, localIso, buildActualTimestamp } from "./utils";

const API_BASE = getApiBase();

// npm run dev
function App() {
  const [selected, setSelected] = useState<string[]>([]);
  const [calibrationMode, setCalibrationMode] = useState<boolean>(false);
  const [calibrationPoints, setCalibrationPoints] = useState<Array<{timestamp: number, measuredTemp: number, targetTemp: number}>>([]);
  const [showHelp, setShowHelp] = useState<boolean>(false);
  const [showAddSensor, setShowAddSensor] = useState<boolean>(false);
  const [newSensorName, setNewSensorName] = useState<string>("");
  const [newSensorInterval, setNewSensorInterval] = useState<number>(60);
  const [newSensorBuffer, setNewSensorBuffer] = useState<number>(10);
  const [updateInfo, setUpdateInfo] = useState<{available: boolean, changelog: string, commitsBehind: number} | null>(null);
  const [showChangelog, setShowChangelog] = useState<boolean>(false);
  const [updating, setUpdating] = useState<boolean>(false);
  const [version, setVersion] = useState<string>("");
  const [showCalibParams, setShowCalibParams] = useState<boolean>(false);
  const [calibParams, setCalibParams] = useState<CorrectionPoint[] | null>(null);
  const [calibParamsLoading, setCalibParamsLoading] = useState<boolean>(false);
  // Zeit-Kalibrierungsfaktor K (zum Anzeigen/Zurücksetzen im Parameter-Dialog).
  const [timeFactor, setTimeFactor] = useState<number | null>(null);
  // Modal zur Wahl der Kalibrierungsart (Temperatur/Zeit) statt window.confirm.
  const [showCalibrationType, setShowCalibrationType] = useState<boolean>(false);

  // Zeit-Kalibrierung (neben Temperatur-Kalibrierung)
  // "none" | "temperature" | "time" beschreibt die aktive Kalibrier-Modalität.
  const [calibrationType, setCalibrationType] = useState<"none" | "temperature" | "time">("none");
  // Geklickter Zeit-Kalibrierpunkt (angezeigter/gemessener Zeitpunkt).
  const [timeCalibrationPoint, setTimeCalibrationPoint] = useState<{ timestamp: number; measuredTemp: number } | null>(null);
  const [showTimeInput, setShowTimeInput] = useState<boolean>(false);
  const [timeInput, setTimeInput] = useState<string>("");

  const { sensorSessions, data, comments, isLoading, error, fetchSensorData, updateData, updateComments, deleteComment, addNewSensorSession } = useSensorData();

  // Kalibrierungspunkt hinzufügen
  const addCalibrationPoint = async (timestamp: number, measuredTemp: number, targetTemp: number) => {
    const newPoint = { timestamp, measuredTemp, targetTemp };
    const updatedPoints = [...calibrationPoints, newPoint];
    setCalibrationPoints(updatedPoints);
    console.log("Kalibrierpunkt hinzugefügt:", newPoint);

    // NEU: Live-Preview sofort aktualisieren
    // Sende ALLE Punkte (kumulativ) – das Backend berechnet daraus die Preview
    // und überschreibt die bestehende Vorschau (kein Merge, keine DB-Schreibung).
    const originalSession = selected.find(s => !s.endsWith('_calibrated'));
    if (originalSession) {
      try {
        console.log(`🔄 Live-Preview wird aktualisiert...`);
        console.log(`📍 Verwende Original-Session: ${originalSession}`);

        const response = await fetch(buildApiUrl('/calibrate'), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            sensor_session: originalSession,
            calibration_points: updatedPoints.map(p => ({
              measured: p.measuredTemp,
              target: p.targetTemp
            }))
          }),
        });

        if (response.ok) {
          const result = await response.json();
          console.log(`✅ Live-Preview aktualisiert: ${result.message}`);

          // Stelle sicher, dass die Preview-Session ausgewählt ist
          const previewSession = `${originalSession}_calibrated`;
          if (!selected.includes(previewSession)) {
            console.log(`📊 Preview-Session zur Auswahl hinzufügen: ${previewSession}`);
            setSelected(prev => [...prev, previewSession]);
          }
        } else {
          console.error("Fehler beim Live-Preview:", response.statusText);
        }
      } catch (error) {
        console.error("Live-Preview Fehler:", error);
      }
    } else {
      console.warn("Keine Original-Session für Live-Preview gefunden!");
    }
  };

  // Kalibrierung durchführen (finale Anwendung)
  const performCalibration = async () => {
    if (calibrationPoints.length === 0) {
      alert("Bitte mindestens einen Kalibrierpunkt hinzufügen!");
      return;
    }

    if (selected.length === 0 || !selected.some(s => !s.endsWith('_calibrated'))) {
      alert("Bitte mindestens eine Original-Sensor-Session für die finale Kalibrierung auswählen!");
      return;
    }

    // Finde die Original-Session (ohne _calibrated)
    const originalSession = selected.find(s => !s.endsWith('_calibrated'));
    if (!originalSession) {
      alert("Keine Original-Session gefunden!");
      return;
    }

    try {
      // Extrahiere die Sensor-ID aus der Session (z.B. "1" aus "1_16")
      const sensorId = originalSession.split('_')[0];

      console.log(`🔄 Finale Kalibrierung für Sensor-ID: ${sensorId}`);
      console.log(`📍 Original-Session: ${originalSession}`);

      // Berechne Korrekturpunkte und sende Preview
      const calibrateResponse = await fetch(buildApiUrl('/calibrate'), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sensor_session: originalSession,
          calibration_points: calibrationPoints.map(p => ({
            measured: p.measuredTemp,
            target: p.targetTemp
          }))
        }),
      });

      if (!calibrateResponse.ok) {
        throw new Error(`Kalibrierungsberechnung fehlgeschlagen: ${calibrateResponse.statusText}`);
      }

      const calibrateResult = await calibrateResponse.json();
      const correctionPoints = calibrateResult.correction_points; // [{t, delta}, ...]

      console.log(`✅ Korrekturpunkte berechnet:`, correctionPoints);

      // Speichere Korrekturpunkte in Datenbank
      const calibrationResponse = await fetch(buildApiUrl('/calibration'), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sensor_id: sensorId,
          correction_points: JSON.stringify(correctionPoints)
        }),
      });

      if (!calibrationResponse.ok) {
        throw new Error(`Kalibrierung fehlgeschlagen: ${calibrationResponse.statusText}`);
      }

      // Kalibrierungsmodus beenden und Punkte zurücksetzen
      setCalibrationMode(false);
      setCalibrationPoints([]);

      // Entferne Preview-Session aus Auswahl
      setSelected(prev => prev.filter(s => !s.endsWith('_calibrated')));

      alert(`Kalibrierung erfolgreich angewendet! ${calibrationPoints.length} Punkte`);

    } catch (error) {
      console.error("Kalibrierungsfehler:", error);
      alert("Fehler bei der finalen Kalibrierung. Siehe Konsole für Details.");
    }
  };

  // Bisherige Kalibrierparameter (Korrekturpunkte) aus der DB laden
  const fetchCalibParams = async () => {
    const originalSession = selected.find(s => !s.endsWith('_calibrated'));
    if (!originalSession) {
      alert("Bitte mindestens eine Original-Sensor-Session auswählen!");
      return;
    }
    const sensorId = originalSession.split('_')[0];
    setCalibParamsLoading(true);
    try {
      // /calibration erwartet sensor_id als Query-Parameter
      const res = await fetch(buildApiUrl(`/calibration?sensor_id=${encodeURIComponent(sensorId)}`));
      if (!res.ok) {
        throw new Error(`Fehler beim Laden: ${res.statusText}`);
      }
      const data = await res.json();
      // data ist ein Array: [{calibration: ..., correction_points: "..."}]
      const latest = Array.isArray(data) && data.length > 0 ? data[data.length - 1] : null;
      const points = parseCorrectionPoints(latest?.correction_points);
      setCalibParams(points);
      // Zeit-Kalibrierungsfaktor K laden (falls vorhanden), sonst null (K=1.0).
      try {
        const tRes = await fetch(buildApiUrl(`/time_calibration?sensor_id=${encodeURIComponent(sensorId)}`));
        if (tRes.ok) {
          const tData = await tRes.json();
          // Faktor 1.0 bedeutet "keine Korrektur" -> null anzeigen.
          setTimeFactor(
            typeof tData?.factor === "number" && tData.factor !== 1.0
              ? tData.factor
              : null
          );
        } else {
          setTimeFactor(null);
        }
      } catch {
        setTimeFactor(null);
      }
      setShowCalibParams(true);
    } catch (error) {
      console.error("Fehler beim Laden der Kalibrierparameter:", error);
      alert("Fehler beim Laden der Kalibrierparameter. Siehe Konsole für Details.");
    } finally {
      setCalibParamsLoading(false);
    }
  };

  // Temperatur-Kalibrierung: einen Korrekturpunkt aus der DB entfernen und neu speichern.
  const deleteCalibrationPoint = async (index: number) => {
    const originalSession = getOriginalSession();
    if (!originalSession) {
      alert("Bitte mindestens eine Original-Sensor-Session auswählen!");
      return;
    }
    if (!calibParams) return;
    if (!confirm(`Korrekturpunkt Nr. ${index + 1} (gemessen ${calibParams[index].t.toFixed(2)} °C) löschen?`)) {
      return;
    }
    const sensorId = originalSession.split('_')[0];
    try {
      const remaining = calibParams.filter((_, i) => i !== index);
      const res = await fetch(buildApiUrl('/calibration'), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sensor_id: sensorId,
          correction_points: JSON.stringify(remaining),
        }),
      });
      if (!res.ok) {
        throw new Error(`Fehler beim Löschen: ${res.statusText}`);
      }
      setCalibParams(remaining);
      fetchSensorData(selected.filter(s => !s.endsWith('_calibrated')));
      alert("Korrekturpunkt gelöscht.");
    } catch (error) {
      console.error("Fehler beim Löschen des Korrekturpunkts:", error);
      alert("Fehler beim Löschen des Korrekturpunkts. Siehe Konsole für Details.");
    }
  };

  // Zeit-Kalibrierung: Faktor K auf 1.0 (kein Faktor) zuruecksetzen.
  const resetTimeFactor = async () => {
    const originalSession = getOriginalSession();
    if (!originalSession) {
      alert("Bitte mindestens eine Original-Sensor-Session auswählen!");
      return;
    }
    if (!confirm("Zeit-Kalibrierung zuruecksetzen (Faktor K auf 1.0)?")) {
      return;
    }
    const sensorId = originalSession.split('_')[0];
    try {
      const res = await fetch(buildApiUrl(`/time_calibration?sensor_id=${encodeURIComponent(sensorId)}`), {
        method: "DELETE",
      });
      if (!res.ok) {
        throw new Error(`Fehler beim Zuruecksetzen: ${res.statusText}`);
      }
      setTimeFactor(null);
      fetchSensorData(selected.filter(s => !s.endsWith('_calibrated')));
      alert("Zeit-Kalibrierung zurueckgesetzt (K=1.0).");
    } catch (error) {
      console.error("Fehler beim Zuruecksetzen der Zeit-Kalibrierung:", error);
      alert("Fehler beim Zuruecksetzen der Zeit-Kalibrierung. Siehe Konsole für Details.");
    }
  };

  // Original-Session (ohne _calibrated) aus der aktuellen Auswahl bestimmen.
  const getOriginalSession = () => selected.find(s => !s.endsWith('_calibrated'));

  // Kalibrierungsmodus in der gewaehlten Art (Temperatur/Zeit) starten.
  const startCalibration = (type: "temperature" | "time") => {
    setCalibrationMode(true);
    setCalibrationType(type);
    setCalibrationPoints([]);
    setTimeCalibrationPoint(null);
    setShowTimeInput(false);
    setShowCalibrationType(false);
  };

  // Kalibrierungsmodus beenden und alle temporären Kalibrier-Zustände aufraeumen.
  const cancelCalibration = () => {
    setCalibrationMode(false);
    setCalibrationType("none");
    setCalibrationPoints([]);
    setTimeCalibrationPoint(null);
    setShowTimeInput(false);
    // Entferne Preview-Sessions aus Auswahl.
    setSelected(prev => prev.filter(s => !s.endsWith('_calibrated')));
  };

  // Zeit-Kalibrierung: geklickter Punkt -> Zeit-Eingabe-Modal oeffnen.
  const handleTimeCalibrationPoint = (timestamp: number, measuredTemp: number) => {
    setTimeCalibrationPoint({ timestamp, measuredTemp });
    // Default: Uhrzeit des geklickten (angezeigten) Punkts vorausfuellen.
    const d = new Date(timestamp);
    const pad = (n: number) => String(n).padStart(2, '0');
    setTimeInput(`${pad(d.getHours())}:${pad(d.getMinutes())}`);
    setShowTimeInput(true);
  };

  // Zeit-Kalibrierung: Preview (nicht persistent) senden.
  const submitTimeCalibration = async () => {
    if (!timeCalibrationPoint) return;
    const originalSession = getOriginalSession();
    if (!originalSession) {
      alert("Bitte mindestens eine Original-Sensor-Session auswählen!");
      return;
    }
    const measured = new Date(timeCalibrationPoint.timestamp);
    const actual = buildActualTimestamp(measured, timeInput);
    if (!actual) {
      alert("Bitte eine gueltige Zeit im Format HH:mm (oder HH:mm:ss) eingeben!");
      return;
    }
    try {
      const res = await fetch(buildApiUrl('/calibrate_time'), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sensor_session: originalSession,
          measured_timestamp: localIso(measured),
          actual_timestamp: localIso(actual),
        }),
      });
      const result = await res.json();
      if (!res.ok || result.status === "error") {
        alert(result.message || "Fehler bei der Zeit-Kalibrierung-Vorschau.");
        return;
      }
      // Preview-Session (falls noch nicht) in Auswahl aufnehmen, damit sie angezeigt wird.
      const previewSession = `${originalSession}_calibrated`;
      if (!selected.includes(previewSession)) {
        setSelected(prev => [...prev, previewSession]);
      }
      console.log(`✅ Zeit-Kalibrierungs-Preview: ${result.message}`);
    } catch (err) {
      console.error("Zeit-Kalibrierungs-Preview Fehler:", err);
      alert("Fehler bei der Zeit-Kalibrierung-Vorschau. Siehe Konsole.");
    }
  };

  // Zeit-Kalibrierung: final speichern (persistiert Faktor).
  const performTimeCalibration = async () => {
    if (!timeCalibrationPoint) {
      alert("Bitte zuerst einen Punkt anklicken und eine Zeit eingeben!");
      return;
    }
    const originalSession = getOriginalSession();
    if (!originalSession) {
      alert("Bitte mindestens eine Original-Sensor-Session auswählen!");
      return;
    }
    const measured = new Date(timeCalibrationPoint.timestamp);
    const actual = buildActualTimestamp(measured, timeInput);
    if (!actual) {
      alert("Bitte eine gueltige Zeit im Format HH:mm (oder HH:mm:ss) eingeben!");
      return;
    }
    try {
      const res = await fetch(buildApiUrl('/calibrate_time/apply'), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sensor_session: originalSession,
          measured_timestamp: localIso(measured),
          actual_timestamp: localIso(actual),
        }),
      });
      const result = await res.json();
      if (!res.ok || result.status === "error") {
        alert(result.message || "Fehler beim Speichern der Zeit-Kalibrierung.");
        return;
      }
      // Kalibriermodus beenden, Zeit-Punkt und Preview aufraeumen.
      setCalibrationMode(false);
      setCalibrationType("none");
      setTimeCalibrationPoint(null);
      setShowTimeInput(false);
      setSelected(prev => prev.filter(s => !s.endsWith('_calibrated')));
      // Daten neu laden, damit der (jetzt persistent angewandte) Faktor sofort sichtbar ist.
      fetchSensorData(selected.filter(s => !s.endsWith('_calibrated')));
      alert(result.message || "Zeit-Kalibrierung gespeichert!");
    } catch (err) {
      console.error("Zeit-Kalibrierung (Apply) Fehler:", err);
      alert("Fehler beim Speichern der Zeit-Kalibrierung. Siehe Konsole.");
    }
  };

  useWebSocket(
  `${API_BASE.replace(/^http/, 'ws')}/ws`,
    (message) => {
      console.log("App: WebSocket-Nachricht empfangen (vollständig):", JSON.stringify(message, null, 2));

      if (message.data?.action === "new_measurements") {
        const sensor_session = message.sensor_session;
        const {timestamps, temperatures} = message.data;
        updateData(sensor_session, timestamps, temperatures);
      } else if (message.data?.action === "update_measurements") {
        // NEU: Behandlung für Live-Kalibrierungsvorschau
        const sensor_session = message.sensor_session;
        const {timestamps, temperatures, calibration_points, strategy} = message.data;
        console.log(`🔧 Live-Kalibrierungsvorschau: ${sensor_session}`);
        console.log(`📊 Strategie: ${strategy}`);
        console.log(`📍 Kalibrierpunkte: ${calibration_points}`);
        
        // Ersetze die Daten komplett für Kalibrierungs-Previews
        updateData(sensor_session, timestamps, temperatures, true);
      } else if (message.data?.action === "add_comment"){
        const sensor_session = message.sensor_session;
        const {timestamp, temperature, comment } = message.data;
        updateComments(sensor_session, timestamp, temperature, comment);
      } else if (message.data?.action === "delete_comment") {
        const sensor_session = message.sensor_session;
        const {timestamp, temperature, comment} = message.data;
        deleteComment(sensor_session, timestamp, temperature, comment);
      } else if (message.data?.action === "new_sensor_session") {
        const newSession = message.data.sensor_session;
        const customText = message.data.custom_text || ""; // Default to empty string if not provided
        if (newSession) {
          addNewSensorSession({ sensor_session: newSession, custom_text: customText });
        } else {
          console.warn("App: 'new_sensor_session' action received without a valid sensor_session.");
        }
      } else {
        console.warn("App: Unbekannte Aktion in der Nachricht:", JSON.stringify(message, null, 2));
      }
    }
  );

  // Globaler F1/ESC Listener für Hilfe-Overlay
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'F1') {
        e.preventDefault();
        e.stopPropagation();
        setShowHelp(true);
      } else if (e.key === 'Escape' && showHelp) {
        e.preventDefault();
        e.stopPropagation();
        setShowHelp(false);
      }
    };
    window.addEventListener('keydown', onKeyDown, { capture: true });
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true } as any);
  }, [showHelp]);

  // Version laden
  useEffect(() => {
    fetch(buildApiUrl('/version'))
      .then(r => r.json())
      .then(d => setVersion(d.version))
      .catch(() => setVersion('unknown'));
  }, []);

  // Update-Check alle 60 Sekunden
  useEffect(() => {
    const checkUpdate = async () => {
      try {
        const response = await fetch(buildApiUrl('/update/check'));
        if (response.ok) {
          const data = await response.json();
          setUpdateInfo({
            available: data.update_available,
            changelog: data.changelog,
            commitsBehind: data.commits_behind
          });
        }
      } catch (err) {
        console.error('Update-Check fehlgeschlagen:', err);
      }
    };
    checkUpdate();
    const interval = setInterval(checkUpdate, 60000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ height: "100vh", width: "100vw", position: "relative", overflow: "hidden" }}>
      <header style={{ position: "relative", top: "3vh", left: "1.5vw", zIndex: 0, margin: 0 }}>
        {version && (
          <div style={{
            position: "absolute", top: "-2.5vh", left: 0,
            fontSize: "1.5vh", color: "#888", fontFamily: "monospace"
          }}>
            {version}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "1vw" }}>
          <svg width="4vh" height="4vh" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <g id="sn-arm">
                <line x1="50" y1="12" x2="50" y2="50"/>
                <polyline points="44,22 50,26 56,22"/>
                <polyline points="43,32 50,35 57,32"/>
              </g>
            </defs>
            <g stroke="#007bff" strokeWidth="2.5" strokeLinecap="round" fill="none" transform="translate(50,50) scale(1.2) translate(-50,-50)">
              <use href="#sn-arm"/>
              <use href="#sn-arm" transform="rotate(60,50,50)"/>
              <use href="#sn-arm" transform="rotate(120,50,50)"/>
              <use href="#sn-arm" transform="rotate(180,50,50)"/>
              <use href="#sn-arm" transform="rotate(240,50,50)"/>
              <use href="#sn-arm" transform="rotate(300,50,50)"/>
            </g>
          </svg>
          <h1 style={{ margin: 0, fontSize: "5vh" }}>Temperaturdaten</h1>
        </div>
        <div style={{ position: "absolute", top: 0, left: "25vw", textAlign: "left" }}>
          {sensorSessions && sensorSessions.length > 0 && sensorSessions
            .filter((session) => session.sensor_session && session.sensor_session.startsWith("None_"))
            .map((session) => (
              <div
                key={session.sensor_session}
                onClick={() => {
                  const customText = session.custom_text || "";
                  setNewSensorName(customText);
                  setShowAddSensor(true);
                }}
                style={{
                  marginBottom: "0.5vh",
                  padding: "0.5vh 0.8vw",
                  border: "2px solid #007bff",
                  borderRadius: "8px",
                  backgroundColor: "#e6f7ff",
                  fontSize: "2vh",
                  color: "#003a8c",
                  width: "20vw",
                  cursor: "pointer",
                }}
                title="Klicken zum Bearbeiten"
              >
                <strong>Session:</strong> {session.sensor_session.replace("None_", "")} <br />
                <strong>Text:</strong> {session.custom_text || "Kein Text angegeben"}
              </div>
            ))}
        </div>
      </header>
      <div style={{ position: "absolute", top: "1vh", right: "1vw", zIndex: 1 }}>
        <div style={{ display: "flex", gap: "1vw", alignItems: "center" }}>
          {calibrationMode && calibrationType === "temperature" && (
            <>
              <div style={{ 
                padding: "0.5vh 1vw", 
                backgroundColor: "#fff3cd", 
                border: "1px solid #ffeaa7",
                borderRadius: "1vh",
                fontSize: "2vh",
                color: "#856404"
              }}>
                Kalibrierpunkte: {calibrationPoints.length}
              </div>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#17a2b8",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={fetchCalibParams}
                disabled={calibParamsLoading}
              >
                {calibParamsLoading ? "Lädt..." : "Parameter anzeigen"}
              </button>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#28a745",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={performCalibration}
                disabled={calibrationPoints.length === 0}
              >
                Kalibrierung anwenden
              </button>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#6c757d",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={cancelCalibration}
              >
                Abbrechen
              </button>
            </>
          )}
          {calibrationMode && calibrationType === "time" && (
            <>
              <div style={{
                padding: "0.5vh 1vw",
                backgroundColor: "#d1ecf1",
                border: "1px solid #b0d7e8",
                borderRadius: "1vh",
                fontSize: "2vh",
                color: "#0c5460"
              }}>
                {timeCalibrationPoint
                  ? `Punkt: ${new Date(timeCalibrationPoint.timestamp).toLocaleString()}`
                  : "Punkt auf Kurve klicken..."}
              </div>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#17a2b8",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={fetchCalibParams}
                disabled={calibParamsLoading}
              >
                {calibParamsLoading ? "Lädt..." : "Parameter anzeigen"}
              </button>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#28a745",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={performTimeCalibration}
                disabled={!timeCalibrationPoint}
              >
                Anwenden
              </button>
              <button
                style={{
                  padding: "1vh 1vw",
                  fontSize: "3vh",
                  backgroundColor: "#6c757d",
                  color: "white",
                  border: "none",
                  borderRadius: "2vh",
                  cursor: "pointer",
                }}
                onClick={cancelCalibration}
              >
                Abbrechen
              </button>
            </>
          )}
          <button
            style={{
              padding: "1vh 1vw",
              fontSize: "4vh",
              backgroundColor: calibrationMode ? "#dc3545" : "#dc3545",
              color: "white",
              border: "none",
              borderRadius: "2vh",
              cursor: "pointer",
            }}
            onClick={async () => {
              if (calibrationMode) {
                cancelCalibration();
              } else {
                if (!selected.some(s => !s.endsWith('_calibrated'))) {
                  alert("Bitte mindestens eine Original-Sensor-Session für die Kalibrierung auswählen!");
                  return;
                }
                const pw = window.prompt("Bitte Admin-Passwort eingeben:");
                if (!pw) return;
                const response = await fetch(buildApiUrl('/verify_password'), {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ password: pw })
                });
                if (!response.ok) {
                  alert("Falsches Passwort oder Serverfehler!");
                  return;
                }
                const data = await response.json();
                if (!data.valid) {
                  alert("Falsches Passwort!");
                  return;
                }
                // Nach der Freischaltung: Art der Kalibrierung waehlen (Modal mit zwei Buttons).
                setShowCalibrationType(true);
              }
            }}
          >
            {calibrationMode ? "Kalibrierung beenden" : "Kalibrierungsmodus"}
          </button>
        </div>
      </div>
      <div style={{ paddingTop: "6vh", display: "flex", gap: "2vw" }}>
        <div style={{ width: "10vw", height: "86vh", display: "flex", flexDirection: "column", gap: "0.5vh", minHeight: 0 }}>
          <div style={{ flex: 1, minHeight: 0 }}>
            <SensorSelector
              sensorSessions={sensorSessions}
              selected={selected}
              onChange={(values) => {
                setSelected(values);
                fetchSensorData(values);
              }}
            />
          </div>
          <button
            onClick={() => setShowAddSensor(true)}
            style={{
              padding: "calc(1vh) calc(2vh)",
              width: "100%",
              backgroundColor: "#007bff",
              color: "white",
              border: "none",
              borderRadius: "calc(1vh)",
              cursor: "pointer",
              fontSize: "calc(2vh)"
            }}
          >
            Neuen Sensor hinzufügen
          </button>
        </div>
        <div style={{ flex: 1 }}>
          {isLoading && <div>Loading...</div>}
          {error && <div style={{ color: "red" }}>{error}</div>}
          {selected.length > 0 && (
            <Chart
              data={data}
              comments={comments}
              calibrationMode={calibrationMode}
              calibrationPoints={calibrationPoints}
              onCalibrationPoint={addCalibrationPoint}
              timeCalibrationMode={calibrationType === "time"}
              onTimeCalibrationPoint={handleTimeCalibrationPoint}
            />
          )}
        </div>
      </div>

      {showAddSensor && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowAddSensor(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', padding: '24px 28px',
              borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
              minWidth: 360
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>Neuen Sensor hinzufügen</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <label>
                Name
                <input
                  type="text"
                  value={newSensorName}
                  onChange={(e) => setNewSensorName(e.target.value)}
                  style={{ width: '100%', padding: '6px 8px', marginTop: 4, fontSize: 16, boxSizing: 'border-box' }}
                  placeholder="z.B. test 123"
                />
              </label>
              <label>
                Messintervall (Sekunden)
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={newSensorInterval}
                  onChange={(e) => setNewSensorInterval(Number(e.target.value))}
                  style={{ width: '100%', padding: '6px 8px', marginTop: 4, fontSize: 16, boxSizing: 'border-box' }}
                />
              </label>
              <label>
                Senden nach (Messpunkten)
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={newSensorBuffer}
                  onChange={(e) => setNewSensorBuffer(Number(e.target.value))}
                  style={{ width: '100%', padding: '6px 8px', marginTop: 4, fontSize: 16, boxSizing: 'border-box' }}
                />
              </label>
            </div>
            <div style={{ display: 'flex', gap: 12, marginTop: 20, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowAddSensor(false)}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: '1px solid #ccc', borderRadius: 8, background: '#f8f8f8', color: '#222'
                }}
              >
                Abbrechen
              </button>
              <button
                onClick={async () => {
                  if (!newSensorName.trim()) { alert("Bitte Namen angeben."); return; }
                  try {
                    const response = await fetch(buildApiUrl('/start_sensor'), {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        custom_text: newSensorName.trim(),
                        messintervall: newSensorInterval,
                        sendpuffer: newSensorBuffer
                      })
                    });
                    if (!response.ok) throw new Error(`Fehler: ${response.statusText}`);
                    const data = await response.json();
                    console.log("Response from server:", data);
                    setShowAddSensor(false);
                  } catch (error) {
                    console.error("Error while adding sensor:", error);
                  }
                }}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: 'none', borderRadius: 8, background: '#007bff', color: 'white'
                }}
              >
                Hinzufügen
              </button>
            </div>
          </div>
        </div>
      )}

      {showHelp && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowHelp(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', width: '70vw', maxWidth: 900,
              maxHeight: '80vh', overflow: 'auto', borderRadius: 12,
              boxShadow: '0 10px 30px rgba(0,0,0,0.25)', padding: '24px 28px'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h2 style={{ margin: 0 }}>Hilfe & Tastenkürzel</h2>
              <button
                onClick={() => setShowHelp(false)}
                style={{
                  border: 'none', background: '#eee', color: '#333', cursor: 'pointer',
                  padding: '6px 10px', borderRadius: 8, fontSize: 14
                }}
                aria-label="Hilfe schließen"
              >
                Schließen (Esc)
              </button>
            </div>

            <div style={{ lineHeight: 1.6, fontSize: 16 }}>
              <p>Kurzübersicht zur Bedienung der Temperaturansicht:</p>
              <ul>
                <li><strong>F1</strong>: Hilfe öffnen</li>
                <li><strong>Esc</strong>: Zoom zurücksetzen (im Diagramm) bzw. Hilfe schließen (wenn geöffnet)</li>
                <li><strong>Mausrad / Pinch</strong>: Zoomen</li>
                <li><strong>Drag</strong>: Verschieben (Pan)</li>
                <li><strong>Strg halten</strong>: Vertikal statt horizontal zoomen</li>
                <li><strong>Klick auf Datenpunkt</strong>: Kommentar hinzufügen</li>
                <li><strong>Kalibrierungsmodus</strong>: Button oben rechts → Passwort „Admin-Passwort“ → Auswahl „Temperatur/Zeit“</li>
                <li><strong>Temperatur-Kalibrierung</strong>: Kalibrierpunkte setzen (max. 2) → „Kalibrierung anwenden“</li>
                <li><strong>Zeit-Kalibrierung</strong>: Punkt auf Kurve klicken → korrekte Uhrzeit eingeben → „Vorschau“ → „Anwenden“ (korrigiert die Uhr-Abweichung des ESP8266)</li>
                <li><strong>Live-Preview</strong>: Wird als <code>_calibrated</code>-Serie angezeigt</li>
              </ul>
              <p>
                Hinweis: Beim Wechsel zu einer komplett anderen Gruppierung (ohne Überlappung der ausgewählten Sessions) wird der Zoom automatisch zurückgesetzt.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Update-Banner */}
      {updateInfo?.available && (
        <div style={{
          position: 'fixed', bottom: 0, left: 0, right: 0,
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          color: 'white', padding: '12px 24px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          boxShadow: '0 -4px 20px rgba(0,0,0,0.15)', zIndex: 9998,
          fontSize: '14px'
        }}>
          <div>
            <strong>🚀 Update verfügbar!</strong>
            <span style={{ marginLeft: 12, opacity: 0.9 }}>{updateInfo.commitsBehind} neue Commits</span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => setUpdateInfo(null)}
              style={{
                padding: '8px 16px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.3)',
                background: 'transparent', color: 'white', cursor: 'pointer', fontSize: '13px'
              }}
            >
              Ignorieren
            </button>
            <button
              onClick={() => setShowChangelog(true)}
              style={{
                padding: '8px 16px', borderRadius: 6,
                background: 'rgba(255,255,255,0.15)', color: 'white', border: '1px solid rgba(255,255,255,0.3)',
                cursor: 'pointer', fontSize: '13px'
              }}
            >
              Änderungen ansehen
            </button>
            <button
              onClick={async () => {
                const pw = window.prompt('Admin-Passwort:');
                if (!pw) return;
                setUpdating(true);
                try {
                  const response = await fetch(buildApiUrl('/update/pull'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pw })
                  });
                  const data = await response.json();
                  if (data.success) {
                    alert('Update erfolgreich! Server wird neu gestartet...');
                    setTimeout(() => window.location.reload(), 5000);
                  } else {
                    alert('Update fehlgeschlagen: ' + data.message);
                  }
                } catch (err) {
                  alert('Fehler: ' + err);
                } finally {
                  setUpdating(false);
                }
              }}
              disabled={updating}
              style={{
                padding: '8px 16px', borderRadius: 6,
                background: 'white', color: '#764ba2', border: 'none',
                cursor: updating ? 'not-allowed' : 'pointer', fontSize: '13px',
                fontWeight: 'bold', opacity: updating ? 0.6 : 1
              }}
            >
              {updating ? 'Wird aktualisiert...' : 'Jetzt aktualisieren'}
            </button>
          </div>
        </div>
      )}

      {/* Changelog-Dialog */}
      {showChangelog && updateInfo && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowChangelog(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', padding: '24px 28px',
              borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
              maxWidth: 600, width: '80vw'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: 12 }}>📋 Änderungsübersicht</h3>
            <div style={{
              background: '#f8f9fa', padding: 16, borderRadius: 8,
              fontFamily: 'monospace', fontSize: 13, whiteSpace: 'pre-wrap',
              maxHeight: 300, overflow: 'auto', lineHeight: 1.8
            }}>
              {updateInfo.changelog || '(Kein Changelog verfügbar)'}
            </div>
            <div style={{ display: 'flex', gap: 12, marginTop: 20, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowChangelog(false)}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: '1px solid #ccc', borderRadius: 8, background: '#f8f8f8', color: '#222'
                }}
              >
                Schließen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dialog: Bisherige Kalibrierparameter anzeigen */}
      {showCalibParams && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowCalibParams(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', padding: '24px 28px',
              borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
              maxWidth: 500, width: '80vw'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: 12 }}>
              {calibrationType === "time" ? "⏱ Zeit-Kalibrierparameter" : "🌡 Temperatur-Kalibrierparameter"}
            </h3>
            {calibrationType === "time" ? (
              <div style={{ fontSize: 15 }}>
                <p style={{ margin: '0 0 12px', color: '#555' }}>
                  Zeit-Kalibrierungsfaktor <strong>K</strong>:
                </p>
                <div style={{ fontSize: 22, fontFamily: 'monospace', marginBottom: 8 }}>
                  {timeFactor === null ? "1.00 (keine Korrektur)" : timeFactor.toFixed(4)}
                </div>
                <p style={{ margin: '0 0 16px', fontSize: 12, color: '#888' }}>
                  Der Startpunkt bleibt fix; alle weiteren Punkte verschieben sich
                  entsprechend dem Faktor.
                </p>
                <button
                  onClick={resetTimeFactor}
                  style={{
                    padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                    border: 'none', borderRadius: 8, background: '#dc3545', color: 'white'
                  }}
                >
                  🗑 Faktor zurücksetzen (K=1.0)
                </button>
              </div>
            ) : calibParams === null ? (
              <div style={{ color: '#888', fontSize: 15 }}>Keine Daten geladen.</div>
            ) : calibParams.length === 0 ? (
              <div style={{ color: '#888', fontSize: 15 }}>
                Es sind noch keine Korrekturpunkte für diesen Sensor gespeichert.
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 15 }}>
                <thead>
                  <tr style={{ borderBottom: '2px solid #ddd' }}>
                    <th style={{ textAlign: 'left', padding: '8px' }}>Gemessen (°C)</th>
                    <th style={{ textAlign: 'left', padding: '8px' }}>Delta (K)</th>
                    <th style={{ textAlign: 'left', padding: '8px' }}>Soll (°C)</th>
                    <th style={{ textAlign: 'right', padding: '8px' }}>
                      <span title="Korrekturpunkt löschen">🗑</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {calibParams.map((pt, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                      <td style={{ padding: '8px' }}>{pt.t.toFixed(2)}</td>
                      <td style={{ padding: '8px' }}>{pt.delta >= 0 ? '+' : ''}{pt.delta.toFixed(2)}</td>
                      <td style={{ padding: '8px' }}>{(pt.t + pt.delta).toFixed(2)}</td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>
                        <button
                          onClick={() => deleteCalibrationPoint(i)}
                          title={`Korrekturpunkt Nr. ${i + 1} löschen`}
                          style={{
                            padding: '4px 8px', fontSize: 13, cursor: 'pointer',
                            border: '1px solid #ccc', borderRadius: 6, background: '#f8f8f8', color: '#222'
                          }}
                        >
                          🗑
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div style={{ display: 'flex', gap: 12, marginTop: 20, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowCalibParams(false)}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: '1px solid #ccc', borderRadius: 8, background: '#f8f8f8', color: '#222'
                }}
              >
                Schließen
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dialog: Zeit-Kalibrierung – korrekte Zeit eingeben */}
      {showTimeInput && timeCalibrationPoint && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowTimeInput(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', padding: '24px 28px',
              borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
              minWidth: 380
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: 8 }}>⏱ Zeit-Kalibrierung</h3>
            <p style={{ margin: '0 0 12px', fontSize: 14, color: '#555' }}>
              Angezeigter Zeitpunkt des geklickten Punkts:<br />
              <strong>{new Date(timeCalibrationPoint.timestamp).toLocaleString()}</strong>
            </p>
            <label style={{ display: 'block', marginBottom: 16 }}>
              Die korrekte Uhrzeit (HH:mm oder HH:mm:ss):
              <input
                type="text"
                value={timeInput}
                onChange={(e) => setTimeInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submitTimeCalibration(); }}
                style={{ width: '100%', padding: '8px 10px', marginTop: 6, fontSize: 16, boxSizing: 'border-box' }}
                placeholder="z.B. 08:35"
              />
            </label>
            <p style={{ margin: '0 0 16px', fontSize: 12, color: '#888' }}>
              Datum bleibt gleich – nur die Uhrzeit wird korrigiert. Der Startpunkt
              bleibt fix; alle weiteren Punkte verschieben sich entsprechend.
            </p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowTimeInput(false)}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: '1px solid #ccc', borderRadius: 8, background: '#f8f8f8', color: '#222'
                }}
              >
                Abbrechen
              </button>
              <button
                onClick={submitTimeCalibration}
                style={{
                  padding: '8px 16px', fontSize: 14, cursor: 'pointer',
                  border: 'none', borderRadius: 8, background: '#17a2b8', color: 'white'
                }}
              >
                Vorschau
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dialog: Art der Kalibrierung waehlen (Temperatur/Zeit) */}
      {showCalibrationType && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999
          }}
          onClick={() => setShowCalibrationType(false)}
        >
          <div
            style={{
              background: '#fff', color: '#222', padding: '24px 28px',
              borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
              minWidth: 400
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: 8 }}>🔧 Art der Kalibrierung</h3>
            <p style={{ margin: '0 0 18px', fontSize: 14, color: '#555' }}>
              Bitte wahlen, welche Kalibrierung durchgefuehrt werden soll.
            </p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button
                onClick={() => startCalibration("temperature")}
                style={{
                  padding: '10px 18px', fontSize: 14, cursor: 'pointer',
                  border: 'none', borderRadius: 8, background: '#17a2b8', color: 'white',
                  fontWeight: 'bold'
                }}
              >
                🌡 Temperatur
              </button>
              <button
                onClick={() => startCalibration("time")}
                style={{
                  padding: '10px 18px', fontSize: 14, cursor: 'pointer',
                  border: 'none', borderRadius: 8, background: '#6f42c1', color: 'white',
                  fontWeight: 'bold'
                }}
              >
                ⏱ Zeit
              </button>
            </div>
            <p style={{ margin: '16px 0 0', fontSize: 12, color: '#888' }}>
              <strong>Temperatur:</strong> Punkte setzen → „Kalibrierung anwenden“.<br />
              <strong>Zeit:</strong> Punkt auf Kurve klicken → korrekte Uhrzeit eingeben → „Vorschau“ → „Anwenden“.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
