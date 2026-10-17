import { useEffect, useMemo, useRef } from "react";
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Title,
  Tooltip,
  Filler,
  type ChartData,
  type ChartOptions,
  type ChartEvent,
} from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import "chartjs-adapter-date-fns";
import { Line } from "react-chartjs-2";
import type { PowerSelection, PowerStream, PowerStreamSession } from "../hooks/usePowerData";

ChartJS.register(LineElement, PointElement, LinearScale, TimeScale, Title,
  Tooltip, Filler, zoomPlugin);

/**
 * Kontinuierlicher Strom-Stream (Absolutezeit-Achse) - ersetzt das fruehere
 * Zyklus-Overlay:
 *
 *   - EINE durchgehende Kurve: Bucket-Mittelwert (Ladungstraeger) + min/max-
 *     Band (Spikes wie WLAN-Bursts bleiben sichtbar). Datenluecken werden als
 *     Luecken gezeichnet (Null-Punkte im regulaeren Bucket-Raster).
 *   - Session-Baender im Hintergrund (Farbe + Beschriftung: welcher Abschnitt
 *     gehoert zu welcher Firmware-Session).
 *   - Gespeicherte Verbrauchs-Bereiche als blaue Baender mit Label.
 *   - Zoom/Pan per Mausrad/Drag (wenn nicht Live-Modus); im Live-Modus folgt
 *     das Fenster den neuesten 60 s (min/max optionsseitig, kein Sprung).
 *   - "Verbrauch messen": Drag-Markierung im Chart -> onMeasure(fromMs, toMs).
 */

const SESSION_COLORS = ["#007bff", "#28a745", "#fd7e14", "#6f42c1", "#20c997", "#dc3545"];
const SELECTION_COLOR = "rgba(37, 99, 235,";
const MEASURE_COLOR = "rgba(224, 123, 0,";

export interface PowerViewWindow {
  fromMs: number;
  toMs: number;
}

interface Props {
  stream: PowerStream | null;
  /** Aktuelles sichtbares Fenster (Epoch-ms) - optionsseitig am Chart fixiert. */
  viewWindow: PowerViewWindow | null;
  /** Bucket-Raster in ms (wie beim Laden verwendet - fuer Luecken-Raster). */
  bucketMs: number;
  /** Live-Modus: Zoom/Pan aus, Fenster folgt den neuesten Daten. */
  live: boolean;
  /** Auswahlmodus "Verbrauch messen": Drag markiert einen Bereich. */
  measuring: boolean;
  /** Gespeicherte Bereiche (blaue Baender mit Label). */
  selections: PowerSelection[];
  /** Zoom/Pan des Users -> App laedt das Fenster nach (nicht im Live-Modus). */
  onWindowChange?: (win: PowerViewWindow) => void;
  /** Fertige Drag-Markierung (Epoch-ms) -> App berechnet/speichert den Verbrauch. */
  onMeasure?: (range: PowerViewWindow) => void;
  height?: string;
}

/**
 * Schmales Chart-Interface fuer Plugin-/Zoom-Callbacks: chart.js parametrisiert
 * Callbacks generisch ueber keyof ChartTypeRegistry (dort passen striktere
 * Typen nicht); wir brauchen nur die x-Skala + Zeichnen. Callbacks bekommen
 * das chart.js-Objekt untypisiert und casten selbst (einmalig, geprueft).
 */
type StreamChartLike = {
  ctx: CanvasRenderingContext2D;
  chartArea: { left: number; right: number; top: number; bottom: number };
  scales: Record<string, unknown>;
  draw: () => void;
};

/** x-Skala mit den benoetigten Methoden (geprueft vor Benutzung). */
interface XScale {
  min: number;
  max: number;
  getPixelForValue: (v: number) => number;
  getValueForPixel: (p: number) => number;
}

function asStreamChart(raw: unknown): StreamChartLike | null {
  const c = raw as StreamChartLike | null;
  if (!c || typeof c.draw !== "function" || !c.chartArea || !c.scales) return null;
  return c;
}

function getXScale(chart: StreamChartLike): XScale | null {
  const x = chart.scales.x as Partial<XScale> | undefined;
  if (!x || typeof x.getPixelForValue !== "function"
    || typeof x.getValueForPixel !== "function"
    || typeof x.min !== "number" || typeof x.max !== "number") return null;
  return x as XScale;
}

export const PowerStreamChart = ({
  stream, viewWindow, bucketMs, live, measuring, selections,
  onWindowChange, onMeasure, height = "40vh",
}: Props) => {
  const zoomDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Refs fuer die Plugin-Zugriffe (Plugin-Objekt wird einmal erzeugt und
  // liest die aktuellen Props ueber Refs - keine steten Re-Registrationen).
  const measuringRef = useRef(measuring);
  measuringRef.current = measuring;
  const onMeasureRef = useRef(onMeasure);
  onMeasureRef.current = onMeasure;
  const selectionsRef = useRef(selections);
  selectionsRef.current = selections;
  const streamRef = useRef(stream);
  streamRef.current = stream;
  const dragRef = useRef<{ startX: number; curX: number } | null>(null);

  useEffect(() => {
    return () => {
      if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
    };
  }, []);

  /**
   * Datensatz auf regulaerem Bucket-Raster (Null-Punkte in Luecken), damit
   * Datenluecken als Luecken gezeichnet werden statt als Verbindungslinie.
   */
  const chartData = useMemo<ChartData<"line">>(() => {
    const buckets = stream?.buckets ?? [];
    if (buckets.length === 0) return { datasets: [] };
    const step = Math.max(1, bucketMs);
    const winFrom = viewWindow?.fromMs ?? buckets[0].t_ms;
    const winTo = viewWindow?.toMs
      ?? buckets[buckets.length - 1].t_ms + step;
    const firstKey = Math.floor(winFrom / step) * step;
    const lastKey = Math.floor(winTo / step) * step;
    const byKey = new Map(buckets.map((b) => [b.t_ms, b]));
    const t: number[] = [];
    const mean: (number | null)[] = [];
    const min: (number | null)[] = [];
    const max: (number | null)[] = [];
    for (let k = firstKey; k <= lastKey; k += step) {
      const b = byKey.get(k);
      t.push(k);
      mean.push(b ? b.ma_mean : null);
      min.push(b ? b.ma_min : null);
      max.push(b ? b.ma_max : null);
    }
    const toPoints = (ys: (number | null)[]): { x: number; y: number | null }[] =>
      ys.map((y, i) => ({ x: t[i], y }));
    return {
      datasets: [
        { // unteres Band-Ende (unsichtbar)
          label: "min", data: toPoints(min) as never,
          borderColor: "transparent", pointRadius: 0, fill: false, tension: 0,
        },
        { // oberes Band-Ende, Flaeche bis zur min-Linie
          label: "max", data: toPoints(max) as never,
          borderColor: "transparent", backgroundColor: "rgba(224, 123, 0, 0.15)",
          pointRadius: 0, fill: "-1", tension: 0,
        },
        { // Mittelwert-Kurve (Ladungstraeger)
          label: "Strom (mA)", data: toPoints(mean) as never,
          borderColor: "#e07b00", backgroundColor: "#e07b00",
          borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0,
        },
      ],
    };
  }, [stream, bucketMs, viewWindow]);

  // Hintergrund-Plugin: Session-Baender + Labels, gespeicherte Bereiche,
  // aktive Drag-Markierung (alles hinter den Daten gezeichnet).
  const backgroundPlugin = useMemo(() => ({
    id: "powerStreamBackground",
    beforeDatasetsDraw: (raw: unknown) => {
      const chart = asStreamChart(raw);
      if (!chart) return;
      const ctx = chart.ctx;
      const area = chart.chartArea;
      if (!area) return;
      const x = getXScale(chart);
      if (!x) return;
      const s = streamRef.current;

      // Session-Baender: Farbband + vertikale Beschriftung am Bandanfang.
      (s?.sessions ?? []).forEach((sess: PowerStreamSession, i: number) => {
        const start = sess.start_ms;
        const end = sess.end_ms ?? (viewWindow?.toMs ?? x.max);
        const px0 = Math.max(area.left, x.getPixelForValue(Math.max(start, x.min)));
        const px1 = Math.min(area.right, x.getPixelForValue(Math.min(end, x.max)));
        if (px1 <= area.left || px0 >= area.right) return;
        const color = SESSION_COLORS[i % SESSION_COLORS.length];
        ctx.save();
        ctx.fillStyle = `${color}1a`;
        ctx.fillRect(px0, area.top, px1 - px0, area.bottom - area.top);
        ctx.fillStyle = color;
        ctx.font = "11px sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        const label = `Session ${sess.id}${sess.custom_text ? ` · ${sess.custom_text}` : ""}`;
        ctx.translate(px0 + 3, area.top + 4);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(label, 0, 0);
        ctx.restore();
      });

      // Gespeicherte Verbrauchs-Bereiche: blaue Baender + Label unten.
      selectionsRef.current.forEach((sel) => {
        const from = Date.parse(sel.from_ts);
        const to = Date.parse(sel.to_ts);
        const px0 = Math.max(area.left, x.getPixelForValue(Math.max(from, x.min)));
        const px1 = Math.min(area.right, x.getPixelForValue(Math.min(to, x.max)));
        if (px1 <= area.left || px0 >= area.right) return;
        ctx.save();
        ctx.fillStyle = `${SELECTION_COLOR}0f)`;
        ctx.fillRect(px0, area.top, px1 - px0, area.bottom - area.top);
        ctx.strokeStyle = SELECTION_COLOR + "0.8)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(px0, area.top); ctx.lineTo(px0, area.bottom);
        ctx.moveTo(px1, area.top); ctx.lineTo(px1, area.bottom);
        ctx.stroke();
        ctx.fillStyle = "#1d4ed8";
        ctx.font = "11px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        const mid = (px0 + px1) / 2;
        const label = `${sel.label ?? "Bereich"} · ${(sel.charge_uah / 1000).toFixed(2)} mAh`;
        ctx.fillText(label, mid, area.bottom - 2, px1 - px0 - 6);
        ctx.restore();
      });

      // Aktive Drag-Markierung (Verbrauch messen).
      const drag = dragRef.current;
      if (drag) {
        const px0 = Math.max(area.left, Math.min(drag.startX, drag.curX));
        const px1 = Math.min(area.right, Math.max(drag.startX, drag.curX));
        ctx.save();
        ctx.fillStyle = `${MEASURE_COLOR}0.2)`;
        ctx.fillRect(px0, area.top, px1 - px0, area.bottom - area.top);
        ctx.strokeStyle = MEASURE_COLOR + "0.9)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(px0, area.top); ctx.lineTo(px0, area.bottom);
        ctx.moveTo(px1, area.top); ctx.lineTo(px1, area.bottom);
        ctx.stroke();
        ctx.restore();
      }
    },
    afterEvent: (raw: unknown, args: { event: ChartEvent }) => {
      if (!measuringRef.current) return;
      const e = args.event;
      if (e.x == null || e.y == null) return;
      const chart = asStreamChart(raw);
      if (!chart) return;
      const x = getXScale(chart);
      if (!x) return;
      if (e.type === "mousedown") {
        dragRef.current = { startX: e.x, curX: e.x };
        chart.draw();
      } else if (e.type === "mousemove" && dragRef.current) {
        dragRef.current.curX = e.x;
        chart.draw();
      } else if (e.type === "mouseup" && dragRef.current) {
        const fromMs = x.getValueForPixel(Math.min(dragRef.current.startX, e.x));
        const toMs = x.getValueForPixel(Math.max(dragRef.current.startX, e.x));
        dragRef.current = null;
        chart.draw();
        // Mindestbreite (30 ms), sonst Klick statt Auswahl
        if (onMeasureRef.current && toMs - fromMs >= 30) {
          onMeasureRef.current({ fromMs, toMs });
        }
      }
    },
  }), [viewWindow]);

  const hasData = (stream?.buckets ?? []).length > 0;
  const win = viewWindow;

  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    events: ["mousemove", "mouseout", "mousedown", "mouseup", "click"],
    animation: false,
    scales: {
      x: {
        type: "time" as const,
        min: win?.fromMs,
        max: win?.toMs,
        time: { unit: "second", displayFormats: { second: "HH:mm:ss" } },
        title: { display: true, text: "Zeit" },
      },
      y: {
        title: { display: true, text: "Strom (mA)" },
        beginAtZero: true,
      },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        mode: "index" as const,
        intersect: false,
        filter: (item) => item.dataset.label === "Strom (mA)",
        callbacks: {
          title: (items) => {
            const ms = Number(items[0]?.parsed?.x);
            return ms ? new Date(ms).toLocaleTimeString("de-DE") : "";
          },
          label: (ctx) => {
            const raw = ctx.raw as { y: number | null };
            return raw.y == null ? "keine Daten" : `${raw.y.toFixed(2)} mA`;
          },
        },
      },
      zoom: {
        zoom: {
          wheel: { enabled: !live },
          pinch: { enabled: !live },
          mode: "x" as const,
          onZoomComplete: (context: { chart: unknown }) => {
            if (live) return;
            if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current);
            const c = asStreamChart(context.chart);
            const x = c ? getXScale(c) : null;
            if (!x) return;
            zoomDebounceRef.current = setTimeout(() => {
              onWindowChange?.({ fromMs: x.min, toMs: x.max });
            }, 300);
          },
        },
        pan: {
          enabled: !live && !measuring,
          mode: "x" as const,
          onPanComplete: (context: { chart: unknown }) => {
            if (live) return;
            const c = asStreamChart(context.chart);
            const x = c ? getXScale(c) : null;
            if (!x) return;
            onWindowChange?.({ fromMs: x.min, toMs: x.max });
          },
        },
      },
    },
  };

  return (
    <div>
      <div style={{ height }}>
        {hasData ? (
          <Line
            data={chartData}
            options={options}
            plugins={[backgroundPlugin]}
          />
        ) : (
          <div style={{ color: "#888", fontSize: "1.8vh", paddingTop: "10vh" }}>
            Keine Stromdaten im gewaehlten Zeitfenster.
          </div>
        )}
      </div>
      <div style={{ fontSize: "1.6vh", color: "#666", marginTop: 4 }}>
        Mausrad = Zoom, Ziehen = Verschieben
        {measuring ? " · Auswahlmodus: Bereich mit der Maus aufziehen" : ""}
      </div>
    </div>
  );
};
