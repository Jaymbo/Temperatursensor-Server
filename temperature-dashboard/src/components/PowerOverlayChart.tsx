import { useMemo } from "react";
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  Title,
  Tooltip,
  Legend,
  type ChartData,
  type ChartOptions,
} from "chart.js";
import { Line } from "react-chartjs-2";
import { POWER_PHASE_ORDER, type PowerCycle } from "../hooks/usePowerData";

ChartJS.register(LineElement, PointElement, LinearScale, Title, Tooltip, Legend);

/**
 * Oszilloskop-Overlay: alle Zyklen uebereinander, synchronisiert auf t=0
 * (Zyklusanfang = Trigger auf den Stromanstieg). Landmarks als vertikale
 * Referenzlinien-Anker (Punkte auf der ersten Kurve), Phasen-Dauern stehen
 * in der Tabelle darunter (min/median/max ueber alle Zyklen).
 *
 * Bewusst keine TimeScale: die x-Achse ist die Zeit SEIT Trigger in Sekunden,
 * nicht die Absolutezeit (deshalb funktioniert das Ueberlagern ueberhaupt).
 */

const PHASE_LABEL: Record<string, string> = {
  wake: "Wake-Spike",
  measure: "Messung",
  wlan: "WLAN-Connect",
  send_tail: "Senden",
};

const CYCLE_COLORS = [
  "#007bff", "#28a745", "#fd7e14", "#6f42c1", "#20c997",
  "#dc3545", "#17a2b8", "#e83e8c", "#ffc107", "#6c757d",
];

const LANDMARK_LABEL: Record<string, string> = {
  wake: "Trigger",
  spike_end: "Spike-Ende",
  wlan: "WLAN-Start",
  send: "Send-Start",
};

interface Props {
  cycles: PowerCycle[];
  height?: string;
}

export const PowerOverlayChart = ({ cycles, height = "45vh" }: Props) => {
  const chartData = useMemo<ChartData<"line">>(() => {
    const datasets: ChartData<"line">["datasets"] = [];

    // Zyklen-Kurven (letzte zuerst zeichnen, damit die neueste oben liegt).
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
  }, [cycles]);

  const maxT = useMemo(
    () => Math.max(1, ...cycles.map((c) => (c.t_s.length ? c.t_s[c.t_s.length - 1] : 0))),
    [cycles]
  );

  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        type: "linear" as const,
        title: { display: true, text: "Zeit seit Trigger (s)" },
        min: 0,
        max: maxT,
      },
      y: {
        title: { display: true, text: "Strom (mA)" },
        min: 0,
      },
    },
    plugins: {
      legend: { display: cycles.length > 1 },
      tooltip: {
        callbacks: {
          label: (ctx) => `${Number(ctx.parsed.y).toFixed(1)} mA @ t=${Number(ctx.parsed.x).toFixed(3)} s`,
        },
      },
    },
  };

  if (cycles.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Zyklen erkannt (keine Stromdaten oder Schwellwerte zu streng).
      </div>
    );
  }

  return (
    <div>
      <div style={{ height }}>
        <Line data={chartData} options={options} />
      </div>
      {/* Phasen-Legende: Label-Zuordnung der Landmarks */}
      <div style={{ fontSize: "1.6vh", color: "#666", marginTop: 4 }}>
        Phasen: {POWER_PHASE_ORDER.map((p) => `${PHASE_LABEL[p] ?? p}`).join(" → ")}
      </div>
    </div>
  );
};

export { PHASE_LABEL, LANDMARK_LABEL };
