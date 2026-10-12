import { useMemo, useRef, useEffect } from "react";
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  Title,
  Tooltip,
  Legend,
  Filler,
  type ChartData,
  type ChartOptions,
} from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import { Line } from "react-chartjs-2";
import { POWER_PHASE_ORDER, type PowerCycle, type PowerSessionAvg } from "../hooks/usePowerData";

ChartJS.register(LineElement, PointElement, LinearScale, Title, Tooltip, Legend,
  Filler, zoomPlugin);

/**
 * Oszilloskop-Overlay: alle Zyklen uebereinander, synchronisiert auf t=0
 * (Zyklusanfang = Trigger - 5 s Vorlauf). Landmarks als Referenz-Punkte auf
 * der Kurve, Phasen-Dauern stehen in der Tabelle darunter (min/median/max
 * ueber alle Zyklen).
 *
 * Bewusst keine TimeScale: die x-Achse ist die Zeit SEIT Trigger in Sekunden,
 * nicht die Absolutezeit (deshalb funktioniert das Ueberlagern ueberhaupt).
 *
 * Zoom/Pan (chartjs-plugin-zoom, Muster wie im Haupt-Chart):
 *   - Mausrad = x-Zoom, Drag = Pan
 *   - Esc = Zoom zuruecksetzen
 */

const PHASE_LABEL: Record<string, string> = {
  pre_peak: "Grundlast (Vorlauf)",
  spike: "TX-Burst",
  tail: "Rest",
};

const CYCLE_COLORS = [
  "#007bff", "#28a745", "#fd7e14", "#6f42c1", "#20c997",
  "#dc3545", "#17a2b8", "#e83e8c", "#ffc107", "#6c757d",
];

const LANDMARK_LABEL: Record<string, string> = {
  trigger: "Trigger (Peak)",
  spike_end: "Burst-Ende",
};

interface Props {
  cycles: PowerCycle[];
  /** Session-Durchschnitte (Standard-Ansicht mode="avg"). */
  avg?: PowerSessionAvg[];
  height?: string;
  /**
   * Wird nach einem Zoom/Pan mit dem sichtbaren Fenster (Sekunden seit
   * Zyklusanfang) aufgerufen - das Frontend laedt diesen Ausschnitt dann
   * in voller Aufloesung nach (serverseitiges LTTB-Downsampling).
   * `null` = Zoom zurueckgesetzt -> wieder die uebersichtliche Ansicht.
   */
  onZoomWindow?: (win: { startS: number; endS: number } | null) => void;
  /**
   * Aktuelles Zoom-Fenster (Mirror des App-State). Nötig für den Sync nach
   * dem Zoom-Nachladen: Der Server liefert die Daten in ABSOLUTEN
   * Koordinaten (Sekunden seit Zyklusanfang, kein Rebasieren), der Sicht-
   * bereich des Charts wird programmatisch aufs Fenster gesetzt, damit die
   * Achse nach dem Nachladen exakt an der gleichen Position bleibt.
   */
  zoomWindow?: { startS: number; endS: number } | null;
}

export const PowerOverlayChart = ({ cycles, avg: avgProp, height = "45vh", onZoomWindow, zoomWindow }: Props) => {
  const avg = avgProp ?? [];
  const chartRef = useRef<any>(null);
  const zoomDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // true, waehrend der Sichtbereich PROGRAMMATISCH gesetzt wird (Sync nach
  // dem Nachladen). Der programmatische Zoom feuert onZoomComplete -> ohne
  // Flag wuerde das erneut ein Nachladen (Endlosschleife) triggern.
  const suppressReportRef = useRef(false);

  /**
   * Sync nach dem Zoom-Nachladen: Sichtbereich programmatisch auf das
   * ABSOLUTE Fenster setzen. Die Daten kommen fenstergeschnitten, aber in
   * absoluten Koordinaten (kein Rebasieren auf 0) - dadurch bleibt die
   * Achse an der echten Position (linke Kante zeigt die echte Fensterzeit,
   * Pan verschiebt die Kurve sichtbar statt ins Leere).
   */
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!zoomWindow) {
      // Zoom zurueckgesetzt: Gesamtansicht wiederherstellen.
      chart.resetZoom?.();
      return;
    }
    suppressReportRef.current = true;
    try {
      // Der Sync-Effekt laeuft NACH dem Daten-Update (chartData-Memo),
      // sodass der neue Datensatz im Chart ist.
      chart.zoom?.(
        { x: { min: zoomWindow.startS, max: zoomWindow.endS } },
        undefined,
        { suppressUpdate: false },
      );
    } finally {
      suppressReportRef.current = false;
    }
  }, [cycles, avg, zoomWindow]);

  // Esc = Zoom zuruecksetzen (wie im Haupt-Chart) -> uebersichtliche Ansicht.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        chartRef.current?.resetZoom?.();
        if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
        onZoomWindow?.(null);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
    };
  }, [onZoomWindow]);

  const chartData = useMemo<ChartData<"line">>(() => {
    const datasets: ChartData<"line">["datasets"] = [];

    // Standard-Ansicht (mode="avg"): pro Session Mittelwert-Kurve mit
    // +/- Standardabweichung als halbtransparentem Band. Wenige Punkte,
    // Abweichler zwischen Sessions sind direkt als Bandbreite sichtbar.
    if (avg && avg.length > 0) {
      avg.forEach((s, i) => {
        const color = CYCLE_COLORS[i % CYCLE_COLORS.length];
        const points = s.t_s.map((t, k) => ({ x: t, y: s.mean[k] }));
        // Band als klassisches Fill-Muster: lower-Linie (unsichtbar) +
        // upper-Linie mit fill:"-1" -> Flaeche ZWISCHEN den Linien, nicht
        // bis zur Nulllinie. (fill:true ueber das geschlossene Polygon
        // schoett den Bereich unter lower mit ein - das Band "schrumpelt".)
        const bandLower = s.t_s.map((t, k) => ({ x: t, y: s.mean[k] - s.std[k] }));
        const bandUpper = s.t_s.map((t, k) => ({ x: t, y: s.mean[k] + s.std[k] }));
        datasets.push({
          label: `Session ${s.session_id ?? "?"} · Ø ${s.n_cycles} Zyklen`,
          data: points,
          borderColor: color,
          backgroundColor: color,
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
          tension: 0,
        });
        datasets.push({
          label: `±σ Session ${s.session_id ?? "?"}`,
          data: bandLower,
          borderColor: color,
          backgroundColor: `${color}33`,
          borderWidth: 0,
          pointRadius: 0,
          fill: false,
          tension: 0,
        });
        // fill:"-1" = Flaeche zwischen upper- und lower-Linie.
        datasets.push({
          label: `±σ upper Session ${s.session_id ?? "?"}`,
          data: bandUpper,
          borderColor: color,
          backgroundColor: `${color}33`,
          borderWidth: 0,
          pointRadius: 0,
          fill: "-1",
          tension: 0,
        });
      });
      return { datasets };
    }

    // Einzelzyklen-Overlay (mode="cycles", letzte zuerst zeichnen, damit die
    // neueste oben liegt).
    const ordered = [...cycles].reverse();
    ordered.forEach((c, i) => {
      const color = CYCLE_COLORS[i % CYCLE_COLORS.length];
      const points = c.t_s.map((t, k) => ({ x: t, y: c.ma[k] }));
      // Landmark-Marker an der Kurve entlang (x = start_s, y = Kurvenwert dort).
      const markers = Object.entries(c.landmarks)
        .filter(([, lm]) => lm !== null)
        .map(([name, lm]) => {
          const idx = c.t_s.findIndex((t) => t >= lm!.start_s - 1e-6);
          return {
            x: lm!.start_s,
            y: idx >= 0 ? c.ma[idx] : 0,
            name,
          };
        });
      datasets.push({
        label: `Zyklus ${c.start.slice(11, 19)}`,
        data: points,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        fill: false,
        tension: 0,
        // Marker als eigene Punkte (gestylt im zweiten Dataset, damit die
        // Linie nicht unterbrochen wird).
        ...({ landmarkMarkers: markers } as object),
      });
    });

    return { datasets };
  }, [cycles, avg]);

  const maxT = useMemo(
    () => Math.max(
      1,
      ...cycles.map((c) => (c.t_s.length ? c.t_s[c.t_s.length - 1] : 0)),
      ...((avg ?? []).map((s) => (s.t_s.length ? s.t_s[s.t_s.length - 1] : 0))),
    ),
    [cycles, avg]
  );

  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        type: "linear" as const,
        title: { display: true, text: "Zeit seit Zyklusanfang (s)" },
        min: 0,
        max: maxT,
      },
      y: {
        title: { display: true, text: "Strom (mA)" },
        min: 0,
      },
    },
    plugins: {
      legend: { display: cycles.length > 1 || avg.length > 1 },
      tooltip: {
        callbacks: {
          label: (ctx) => `${Number(ctx.parsed.y).toFixed(1)} mA @ t=${Number(ctx.parsed.x).toFixed(3)} s`,
        },
      },
      zoom: {
        zoom: {
          wheel: { enabled: true },
          pinch: { enabled: true },
          mode: "x" as const,
          // Nach dem Zoom das Fenster melden (debounced, damit schnelles
          // Mausraden nicht jede Stufe nachlaedt).
          onZoomComplete: ({ chart }: any) => {
            if (suppressReportRef.current) return;
            if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
            const x = chart.scales.x;
            zoomDebounceRef.current = setTimeout(() => {
              // Daten sind ABSOLUT (kein Rebasieren) -> das sichtbare Fenster
              // kann direkt gemeldet werden.
              onZoomWindow?.({ startS: x.min, endS: x.max });
            }, 400);
          },
        },
        pan: {
          enabled: true,
          mode: "x" as const,
          onPanComplete: ({ chart }: any) => {
            if (suppressReportRef.current) return;
            if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
            const x = chart.scales.x;
            zoomDebounceRef.current = setTimeout(() => {
              // Wie onZoomComplete: Daten sind ABSOLUT, Fenster direkt melden.
              onZoomWindow?.({ startS: x.min, endS: x.max });
            }, 400);
          },
        },
      },
    },
  };

  if (cycles.length === 0 && avg.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Zyklen erkannt (keine Stromdaten oder Schwellwerte zu streng).
      </div>
    );
  }

  return (
    <div>
      <div style={{ height }}>
        <Line ref={chartRef} data={chartData} options={options} />
      </div>
      {/* Bedien-Hinweis + Phasen-Legende: Label-Zuordnung der Landmarks */}
      <div style={{ fontSize: "1.6vh", color: "#666", marginTop: 4 }}>
        Mausrad = Zoom, Ziehen = Verschieben, Esc = Zurücksetzen · Phasen:{" "}
        {POWER_PHASE_ORDER.map((p) => `${PHASE_LABEL[p] ?? p}`).join(" → ")}
      </div>
    </div>
  );
};

export { PHASE_LABEL, LANDMARK_LABEL };
