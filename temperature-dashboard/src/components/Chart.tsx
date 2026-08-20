import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { getColorForString } from "../utils";
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Title,
  Tooltip,
  Legend,
  CategoryScale,
} from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import { Line } from "react-chartjs-2";
import "chartjs-adapter-date-fns";
import { buildApiUrl } from "../api";

ChartJS.register(
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Title,
  Tooltip,
  Legend,
  CategoryScale,
  zoomPlugin
);

interface ExtendedDataPoint {
  x: number; // Unix-Timestamp
  y: number; // Temperatur
  isComment?: boolean;
  isCalibrationPoint?: boolean;
  sensorSession?: string;
  index?: number;
}

interface ExtendedDataset {
  label: string;
  data: ExtendedDataPoint[];
  borderColor: string;
  backgroundColor?: string;
  pointRadius?: number;
  fill: boolean;
  showLine?: boolean;
  tension?: number;
  borderWidth?: number;
}

type Props = {
  data: { [sensor_session: string]: { timestamp: string; temperature: number }[] };
  comments: { [sensor_session: string]: { timestamp: string; temperature: number; comment: string }[] };
  kalibriermodus?: boolean;
  calibrationMode?: boolean;
  calibrationPoints?: Array<{timestamp: number, measuredTemp: number, targetTemp: number}>;
  onCalibrationPoint?: (timestamp: number, measuredTemp: number, targetTemp: number) => void;
  timeCalibrationMode?: boolean;
  onTimeCalibrationPoint?: (timestamp: number, measuredTemp: number) => void;
};


// Zentrale URL Helper wird genutzt (buildApiUrl)

export const Chart: React.FC<Props> = ({
  data,
  comments,
  kalibriermodus,
  calibrationMode = false,
  calibrationPoints: newCalibrationPoints = [],
  onCalibrationPoint,
  timeCalibrationMode = false,
  onTimeCalibrationPoint,
}) => {
  const [commentsState, setComments] = useState(comments);
  const [storedCalibrationPoints, setStoredCalibrationPoints] = useState<{ [sensor_session: string]: { timestamp: string; temperature: number; calibration: string }[] }>({});
  const isCtrlPressedRef = useRef(false);
  const [viewRange, setViewRange] = useState<{min?: number; max?: number}>({});
  const chartRef = useRef<any>(null);
  const lastNonEmptySelectionRef = useRef<Set<string>>(new Set());
  const [yLocked, setYLocked] = useState<boolean>(false);
  const [yRange, setYRange] = useState<{min?: number; max?: number}>({});

  useEffect(() => {
    setComments(comments);
  }, [comments]);

  // Alte (persistente) Kalibrierpunkte holen, nur wenn alter Modus aktiv
  useEffect(() => {
    if (!kalibriermodus) return;
    let abort = false;
    (async () => {
      try {
        const calibrationData: { [sensor_session: string]: { timestamp: string; temperature: number; calibration: string }[] } = {};
        for (const sensorSession of Object.keys(data)) {
          const response = await fetch(buildApiUrl(`/calibration?sensor_session=${sensorSession}`));
          if (!response.ok) continue;
          const sessionData = await response.json();
          calibrationData[sensorSession] = sessionData;
        }
        if (!abort) setStoredCalibrationPoints(calibrationData);
      } catch (e) {
        console.error("Fehler beim Abrufen der Kalibrierdaten", e);
      }
    })();
    return () => { abort = true; };
  }, [kalibriermodus, data]);

  // Reset Zoom, wenn eine komplett andere Gruppierung (kein Overlap) ausgewählt wird
  useEffect(() => {
    const currentKeys = Object.keys(data);
    const currentSet = new Set(currentKeys);
    if (currentSet.size > 0) {
      const prevSet = lastNonEmptySelectionRef.current;
      if (prevSet.size > 0) {
        let hasOverlap = false;
        for (const k of prevSet) { if (currentSet.has(k)) { hasOverlap = true; break; } }
        if (!hasOverlap) {
          // Kompletter Wechsel der Gruppierung -> Zoom zurücksetzen
          const ch: any = chartRef.current as any;
          ch?.resetZoom?.();
          setViewRange({});
          setYLocked(false);
          setYRange({});
          try {
            if (ch?.options?.scales?.y) {
              ch.options.scales.y.min = undefined;
              ch.options.scales.y.max = undefined;
              ch.update?.('none');
            }
          } catch {}
        }
      }
      lastNonEmptySelectionRef.current = currentSet; // nur nicht-leere Sets merken
    }
    // Bei leerer Auswahl behalten wir die letzte nicht-leere Referenz bei
  }, [data]);

  // Tastatur-Listener: Ctrl (Zoom-Achse) und ESC (Zoom zurücksetzen)
  useEffect(() => {
    const handleDown = (e: KeyboardEvent) => {
      if (e.key === "Control") isCtrlPressedRef.current = true;
      if (e.key === "Escape") {
        // Reset Zoom auf ESC und gespeicherten Bereich löschen
        const ch: any = chartRef.current as any;
        ch?.resetZoom?.();
        setViewRange({});
        setYLocked(false);
        setYRange({});
        try {
          if (ch?.options?.scales?.y) {
            ch.options.scales.y.min = undefined;
            ch.options.scales.y.max = undefined;
            ch.update?.('none');
          }
        } catch {}
      }
    };
    const handleUp = (e: KeyboardEvent) => { if (e.key === "Control") isCtrlPressedRef.current = false; };
    window.addEventListener("keydown", handleDown);
    window.addEventListener("keyup", handleUp);
    return () => {
      window.removeEventListener("keydown", handleDown);
      window.removeEventListener("keyup", handleUp);
    };
  }, []);

  const getSingleSensorSession = () => Object.keys(data)[0];

  const handleNewCalibrationClick = useCallback((event: any) => {
    if (!onCalibrationPoint) return;
    const points = event.chart.getElementsAtEventForMode(event.native, "nearest", { intersect: true }, false).reverse();
    if (points.length === 0) return;

    // Ignoriere Klicks auf Kommentare, Kalibrierungspunkte und _calibrated-Kurven
    let point = points[0];
    const dataset = event.chart.data.datasets[point.datasetIndex];
    const dataPoint = dataset.data[point.index];

    if (dataPoint.isComment || dataPoint.isCalibrationPoint) return;

    // Wenn auf _calibrated-Kurve geklickt: Finde Originalkurve und hole Rohwert
    let measuredTemp = dataPoint.y;
    let timestamp = dataPoint.x;
    const label = dataset.label;
    if (label && label.endsWith('_calibrated')) {
      console.log(`🔍 Klick auf _calibrated-Kurve "${label}". Suche Originalkurve...`);
      const originalLabel = label.replace('_calibrated', '');
      const originalDatasetIndex = event.chart.data.datasets.findIndex((ds: any) => ds.label === originalLabel);
      console.log(`🔍 Originalkurve "${originalLabel}" gefunden an Index ${originalDatasetIndex}`);
      if (originalDatasetIndex >= 0) {
        const origDataset = event.chart.data.datasets[originalDatasetIndex];
        console.log(`🔍 Orig-Dataset hat ${origDataset.data.length} Datenpunkte`);
        // Finde den Datenpunkt mit dem nächsten Timestamp
        const closestPoint = origDataset.data.reduce((best: any, dp: any) => {
          const dist = Math.abs(dp.x - timestamp);
          return dist < best.dist ? { dist, y: dp.y } : best;
        }, { dist: Infinity, y: 0 });
        console.log(`🔍 Klick auf korrigiert ${dataPoint.y}°C → Rohwert: ${closestPoint.y}°C`);
        measuredTemp = closestPoint.y;
      } else {
        console.warn(`⚠️ Originalkurve "${originalLabel}" NICHT gefunden! Verwende korrigierten Wert.`);
      }
    }

    if (newCalibrationPoints && newCalibrationPoints.length >= 2) {
      alert("Es sind maximal zwei Kalibrierpunkte erlaubt.");
      return;
    }
    const targetTempStr = window.prompt(
      `Welche Temperatur sollte es zum Zeitpunkt ${new Date(timestamp).toLocaleString()} sein?\n\nGemessene Temperatur (Roh): ${measuredTemp.toFixed(2)}°C`
    );
    if (!targetTempStr) return;
    const targetTemp = parseFloat(targetTempStr);
    if (isNaN(targetTemp)) {
      alert("Bitte geben Sie eine gültige Zahl ein!");
      return;
    }
    console.log(`📤 Kalibrierpunkt: measured=${measuredTemp.toFixed(2)}, target=${targetTemp.toFixed(2)}`);
    onCalibrationPoint(timestamp, measuredTemp, targetTemp);
  }, [newCalibrationPoints, onCalibrationPoint]);

  const handleOldCalibrationClick = useCallback((event: any) => {
    const rect = event.chart.canvas.getBoundingClientRect();
    const x = event.native.x - rect.left;
    const y = event.native.y - rect.top;
    const chartX = event.chart.scales.x.getValueForPixel(x);
    const chartY = event.chart.scales.y.getValueForPixel(y);
    const sensorSession = getSingleSensorSession();
    const count = (storedCalibrationPoints[sensorSession] || []).length;
    if (count >= 2) {
      alert("Es sind maximal zwei Kalibrierpunkte erlaubt.");
      return;
    }
    handleAddCalibrationPoint({ x: chartX, y: chartY });
  }, [storedCalibrationPoints, data]);

  // Zeit-Kalibrierung: Klick auf Datenpunkt -> App fragt die "wirkliche" Zeit.
  // Ignoriert Kommentare, Kalibrierungspunkte und _calibrated-Kurven (sonst
  // waere der angezeigte Zeitpunkt mit K_aktuel korrigiert und die Inversion
  // im Backend nicht mehr konsistent).
  const handleTimeCalibrationClick = useCallback((event: any) => {
    if (!onTimeCalibrationPoint) return;
    const points = event.chart.getElementsAtEventForMode(event.native, "nearest", { intersect: true }, false).reverse();
    if (points.length === 0) return;

    const point = points[0];
    const dataset = event.chart.data.datasets[point.datasetIndex];
    const dataPoint = dataset.data[point.index];
    const label = dataset.label;

    // Ignoriere Kommentare und Kalibrierungspunkte
    if (dataPoint.isComment || dataPoint.isCalibrationPoint) return;
    // Ignoriere _calibrated-Kurven (nur Original/aktuelle Daten sind konsistent)
    if (label && label.endsWith('_calibrated')) return;

    const timestamp = dataPoint.x; // angezeigter (korrigierter) Zeitpunkt
    const measuredTemp = dataPoint.y;
    console.log(`⏱ Zeit-Kalibrierungspunkt: ${new Date(timestamp).toLocaleString()} / ${measuredTemp}°C`);
    onTimeCalibrationPoint(timestamp, measuredTemp);
  }, [onTimeCalibrationPoint]);

  const handleCommentModeClick = useCallback((event: any) => {
    const points = event.chart.getElementsAtEventForMode(event.native, "nearest", { intersect: true }, false).reverse();
    if (points.length > 0) {
      const point = points[0];
      const dataset = event.chart.data.datasets[point.datasetIndex];
      const dataPoint = dataset.data[point.index];
      if (dataPoint.isComment) {
        handleDeleteComment(dataPoint.sensorSession!, dataPoint.index!);
        return;
      }
      handleAddComment({ x: new Date(dataPoint.x).getTime(), y: dataPoint.y });
      return;
    }
    const rect = event.chart.canvas.getBoundingClientRect();
    const x = event.native.x - rect.left;
    const y = event.native.y - rect.top;
    const chartX = event.chart.scales.x.getValueForPixel(x);
    const chartY = event.chart.scales.y.getValueForPixel(y);
    handleAddComment({ x: chartX, y: chartY });
  }, [commentsState, data]);

  const handleChartClick = useCallback((event: any) => {
    // Zeit-Kalibrierung hat Vorrang (calibrationMode ist in beiden Modi true).
    if (timeCalibrationMode) return handleTimeCalibrationClick(event);
    if (calibrationMode) return handleNewCalibrationClick(event);
    if (kalibriermodus) return handleOldCalibrationClick(event);
    return handleCommentModeClick(event);
  }, [timeCalibrationMode, calibrationMode, kalibriermodus, handleTimeCalibrationClick, handleNewCalibrationClick, handleOldCalibrationClick, handleCommentModeClick]);

  const handleAddComment = ({ x, y }: { x: number; y: number }) => {
    const sensorSession = getSingleSensorSession();
    const commentText = window.prompt("Bitte geben Sie Ihren Kommentar ein:");
    if (!commentText) return;
    const localTimestamp = new Date(x - new Date().getTimezoneOffset() * 60000).toISOString().replace('Z', '');
    const newComment = { sensor_session: sensorSession, timestamp: localTimestamp, temperature: y, comment: commentText };
    // Optimistisch updaten
    setComments(prev => ({ ...prev, [sensorSession]: [ ...(prev[sensorSession] || []), newComment ] }));
  fetch(buildApiUrl("/comments"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newComment),
    }).catch(() => {
      alert("Fehler beim Hinzufügen des Kommentars.");
      setComments(prev => ({
        ...prev,
        [sensorSession]: (prev[sensorSession] || []).filter(c => c !== newComment)
      }));
    });
  };

  const handleDeleteComment = (sensorSession: string, commentIndex: number) => {
    if (!commentsState[sensorSession]) return;
    if (!window.confirm("Möchten Sie diesen Kommentar wirklich löschen?")) return;
    const updatedComments = { ...commentsState };
    const [deletedComment] = updatedComments[sensorSession].splice(commentIndex, 1);
    setComments(updatedComments);
  fetch(buildApiUrl("/comments"), {
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        sensor_session: sensorSession,
        timestamp: deletedComment.timestamp,
        temperature: deletedComment.temperature,
        comment: deletedComment.comment,
      }),
    }).catch(() => {
        alert("Fehler beim Löschen des Kommentars.");
        updatedComments[sensorSession].splice(commentIndex, 0, deletedComment);
        setComments(updatedComments);
      });
  };

  const handleAddCalibrationPoint = ({ x, y }: { x: number; y: number }) => {
    const sensorSession = getSingleSensorSession();
    const calibrationText = window.prompt("Bitte geben Sie die Kalibrierungsdetails ein (z. B. Faktor oder Offset):");
    if (!calibrationText) return;

    const newCalibrationPoint = {
      sensor_session: sensorSession,
      timestamp: new Date(x).toISOString(),
      temperature: y,
      calibration: calibrationText,
    };

    setStoredCalibrationPoints((prev) => {
      const updated = { ...prev };
      if (!updated[sensorSession]) {
        updated[sensorSession] = [];
      }
      // Maximal zwei Punkte erlauben
      if (updated[sensorSession].length >= 2) {
        alert("Es sind maximal zwei Kalibrierpunkte erlaubt.");
        return updated;
      }
      updated[sensorSession].push(newCalibrationPoint);

      return updated;
    });
  };

  const chartData = useMemo<{ datasets: ExtendedDataset[] }>(() => {
    const datasets: ExtendedDataset[] = Object.keys(data).map(sensor_session => ({
      label: sensor_session,
      data: data[sensor_session].map(entry => ({ x: new Date(entry.timestamp).getTime(), y: entry.temperature })),
      borderColor: getColorForString(sensor_session),
      fill: false,
      tension: 0.1,
      borderWidth: 1,
      showLine: true,
    }));

    Object.keys(commentsState).forEach(sensor_session => {
      if (!data[sensor_session]) return; // nur Kommentare für vorhandene Serien
      commentsState[sensor_session].forEach((comment, index) => {
        datasets.push({
          label: `${comment.comment}`,
            data: [{
              x: new Date(comment.timestamp).getTime(),
              y: comment.temperature,
              isComment: true,
              sensorSession: sensor_session,
              index,
            }],
            borderColor: getColorForString(comment.comment),
            backgroundColor: getColorForString(comment.comment),
            pointRadius: 5,
            fill: false,
        });
      });
    });

    // Persistente (alte) Kalibrierpunkte
    Object.keys(storedCalibrationPoints).forEach(sensor_session => {
      storedCalibrationPoints[sensor_session].forEach((point, index) => {
        datasets.push({
          label: `Kalibrierungspunkt`,
          data: [{
            x: new Date(point.timestamp).getTime(),
            y: point.temperature,
            isComment: false,
            sensorSession: sensor_session,
            index,
          }],
          borderColor: "orange",
          backgroundColor: "orange",
          pointRadius: 6,
          fill: false,
        });
      });
    });

    // Neue Kalibrierungspunkte (frontend neu)
    if (newCalibrationPoints && newCalibrationPoints.length > 0) {
      const calibrationData = [...newCalibrationPoints]
        .sort((a,b) => a.timestamp - b.timestamp)
        .map((point, index) => ({
          x: point.timestamp,
          y: point.targetTemp,
          isComment: false,
          isCalibrationPoint: true,
          sensorSession: "calibration",
          index,
        }));
      datasets.push({
        label: `Neue Kalibrierungspunkte (${newCalibrationPoints.length})`,
        data: calibrationData,
        borderColor: "red",
        backgroundColor: "red",
        pointRadius: 9,
        fill: false,
        showLine: true,
        tension: 0,
        borderWidth: 2,
      });
    }

    return { datasets };
  }, [data, commentsState, storedCalibrationPoints, newCalibrationPoints]);

  // (Kalkulierte chartData via useMemo oben) – entfernte Inline-Push-Manipulationen

  // Zeitbereich bestimmen, um die Achsen-Formatierung dynamisch zu wählen
  const { spanMs } = useMemo(() => {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    // Wenn wir eine aktuelle Ansicht (Zoom/Pan) haben, nutze diese
    if (viewRange.min != null && viewRange.max != null) {
      return { spanMs: viewRange.max - viewRange.min };
    }
    Object.values(data).forEach(arr => {
      arr.forEach(e => {
        const t = new Date(e.timestamp).getTime();
        if (!isNaN(t)) { if (t < min) min = t; if (t > max) max = t; }
      });
    });
    // auch neue Kalibrierpunkte berücksichtigen, falls vorhanden
    (newCalibrationPoints || []).forEach(p => {
      if (typeof p.timestamp === 'number') {
        if (p.timestamp < min) min = p.timestamp; if (p.timestamp > max) max = p.timestamp;
      }
    });
    const span = (isFinite(min) && isFinite(max)) ? (max - min) : 0;
    return { spanMs: span };
  }, [data, newCalibrationPoints, viewRange]);

  // Einheit und Anzeigeformate abhängig von der Spannweite (inkl. Sekunden/Millisekunden bei starkem Zoom)
  const timeUnit = spanMs < 3 * 1000
    ? 'millisecond'
    : spanMs < 30 * 1000
    ? 'second'
    : spanMs < 5 * 60 * 1000
      ? 'second'
      : spanMs < 3 * 60 * 60 * 1000
        ? 'minute'
        : spanMs < 3 * 24 * 60 * 60 * 1000
          ? 'hour'
          : 'day';
  const maxTicks = timeUnit === 'millisecond' ? 12 : timeUnit === 'second' ? 18 : timeUnit === 'minute' ? 14 : timeUnit === 'hour' ? 12 : 10;
  const displayFormats: Record<string, string> = {
    millisecond: 'HH:mm:ss.SSS',
    second: 'HH:mm:ss',
    minute: 'HH:mm',
    hour: 'HH:mm',
    day: 'dd.MM.yyyy',
  };

  const fmtFull = (ms: number) => new Date(ms).toLocaleString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtFullHMS = (ms: number) => {
    const d = new Date(ms);
    const date = d.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric' });
    const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return `${date}, ${time}`;
  };
  const fmtHMS = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const fmtHMSS = (ms: number) => {
    const d = new Date(ms);
    const h = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const msPart = String(d.getMilliseconds()).padStart(3, '0');
    return `${h}.${msPart}`;
  };
  const fmtHM = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const fmtDMY = (ms: number) => new Date(ms).toLocaleDateString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric' });

  const tickCallback = (value: any, index: number, ticks: any[]) => {
    const v = typeof value === 'number' ? value : (ticks?.[index]?.value ?? Date.parse(value));
    if (!v) return '';
    if (timeUnit === 'millisecond') {
      // erste links volles Datum mit ms-Auflösung, rest HH:mm:ss.SSS
      const d = new Date(v);
      const date = d.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric' });
      const time = fmtHMSS(v);
      return index === 0 ? `${date}, ${time}` : time;
    }
    if (timeUnit === 'second') {
      // erste links volles Datum bis Sekunden, rest HH:mm:ss
      return index === 0 ? fmtFullHMS(v) : fmtHMS(v);
    }
    if (timeUnit === 'minute') {
      // erste links volles Datum bis Minuten, rest HH:mm
      return index === 0 ? fmtFull(v) : fmtHM(v);
    }
    if (timeUnit === 'hour') {
      // erste links volles Datum bis Minuten, rest nur HH:mm
      return index === 0 ? fmtFull(v) : fmtHM(v);
    }
    // day
    return index === 0 ? fmtDMY(v) : fmtDMY(v);
  };

  const options = {
    responsive: true,
  animation: false as const,
    onClick: handleChartClick,
    scales: {
      x: {
        type: "time" as const,
  time: { unit: timeUnit as any, displayFormats, stepSize: (timeUnit === 'millisecond' || timeUnit === 'second' || timeUnit === 'minute' || timeUnit === 'hour') ? 1 : undefined },
        title: { display: true, text: "Zeit" },
        ticks: {
          autoSkip: true,
          maxTicksLimit: maxTicks,
          callback: tickCallback,
          font: { size: 12 },
          maxRotation: 0,
          autoSkipPadding: 4,
          padding: 6,
        },
    // Bewahre Zoom-/Panbereich auf Re-Renders
    min: (viewRange.min != null ? viewRange.min : undefined) as any,
    max: (viewRange.max != null ? viewRange.max : undefined) as any,
      },
      y: { 
  title: { display: true, text: "Temperatur (°C)" },
  ticks: { font: { size: 12 } },
  min: (yLocked && yRange.min != null ? yRange.min : undefined) as any,
  max: (yLocked && yRange.max != null ? yRange.max : undefined) as any,
      },
    },
    plugins: {
      title: {
        display: true,
        text: timeCalibrationMode
          ? "ZEIT-KALIBRIERUNG - Klicken Sie auf einen Punkt und geben Sie die korrekte Zeit ein"
          : calibrationMode
            ? "NEUER KALIBRIERUNGSMODUS - Klicken Sie auf Datenpunkte um Kalibrierungspunkte zu setzen"
            : kalibriermodus
              ? "ALTER KALIBRIERUNGSMODUS - Klicken zum Hinzufügen von Kalibrierpunkten"
              : "Klicken Sie auf Datenpunkte um Kommentare hinzuzufügen",
        color: timeCalibrationMode ? "#0c5460" : calibrationMode ? "red" : kalibriermodus ? "orange" : "#666",
        font: {
          size: (timeCalibrationMode || calibrationMode || kalibriermodus) ? 16 : 14,
          weight: (timeCalibrationMode || calibrationMode || kalibriermodus) ? "bold" as const : "normal" as const
        }
      },
      zoom: {
        zoom: {
          wheel: {
            enabled: true,
          },
          pinch: { enabled: true },
      mode: () => (isCtrlPressedRef.current ? "y" : "x"),
            onZoomComplete: ({chart}: any) => {
            const x = chart.scales.x;
            setViewRange({ min: x.min, max: x.max });
              // Wenn per Strg in Y gezoomt wurde, Y sperren und Bereich merken
              if (isCtrlPressedRef.current) {
                const y = chart.scales.y;
                setYLocked(true);
                setYRange({ min: y.min, max: y.max });
              } else if (yLocked) {
                // Wenn Y bereits gesperrt ist, halte den aktuellen Bereich fest
                const y = chart.scales.y;
                setYRange({ min: y.min, max: y.max });
              }
          }
        },
        pan: { 
          enabled: true, 
          mode: "xy" as const,
          onPanComplete: ({chart}: any) => {
            const x = chart.scales.x;
            setViewRange({ min: x.min, max: x.max });
              if (yLocked) {
                const y = chart.scales.y;
                setYRange({ min: y.min, max: y.max });
              }
          }
        },
      },
    },
  };

  return (
    <div style={{ width: "85vw", height: "86vh", display: "flex", flexDirection: "column" }}>
  <Line ref={chartRef} data={chartData} options={options} />
    </div>
  );
};