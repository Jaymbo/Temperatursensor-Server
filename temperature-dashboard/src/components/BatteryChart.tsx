import { useMemo } from "react";
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
  type ChartData,
  type ChartOptions,
} from "chart.js";
import { Line } from "react-chartjs-2";
import "chartjs-adapter-date-fns";
import {
  buildBatterySeries,
  buildBatteryMarkers,
  type BatteryReading,
} from "../utils";
import type { BatterySessionMarker } from "../hooks/useBatteryData";

ChartJS.register(
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Title,
  Tooltip,
  Legend,
  CategoryScale
);

/**
 * Akku-Verlauf eines Sensors: Spannung (V) ueber Zeit.
 *
 * Bewusst KEINE Prozent-Achse (LiPo ist zwischen 3,7 und 3,9 V zu flach).
 * Stattdessen:
 *   - 3,15-V-Referenzlinie = bekannte Abschaltgrenze des Loggers
 *   - Marker fuer erkannte Ladevorgaenge und Ausfaelle
 *   - Kennzeichnung von Messungen UNTER LAST (andere Punktfarbe)
 */

// Abschaltgrenze des Loggers (siehe backend/battery_health.CUTOFF_MV).
export const CUTOFF_V = 3.15;

interface Props {
  readings: BatteryReading[];
  sessions?: BatterySessionMarker[];
  height?: string;
}

const MARKER_COLOR: Record<string, string> = {
  charge: "#2e7d32",
  outage: "#d32f2f",
  swap: "#6f42c1",
};

export const BatteryChart = ({ readings, sessions = [], height = "40vh" }: Props) => {
  const chartData = useMemo<ChartData<"line">>(() => {
    const points = buildBatterySeries(readings);
    const markers = buildBatteryMarkers(sessions, points);

    const datasets: ChartData<"line">["datasets"] = [
      {
        label: "Spannung",
        data: points,
        borderColor: "#007bff",
        backgroundColor: "#007bff",
        fill: false,
        tension: 0.2,
        pointRadius: 2,
        // Messungen unter Last anders einfaerben (aussagekraeftiger).
        pointBackgroundColor: points.map((p) =>
          p.under_load ? "#ff7043" : "#007bff"
        ),
      },
    ];

    // 3,15-V-Referenzlinie ueber die gesamte Zeitspanne.
    if (points.length > 0) {
      const xs = points.map((p) => p.x);
      datasets.push({
        label: `Abschaltgrenze ${CUTOFF_V.toFixed(2)} V`,
        data: [
          { x: Math.min(...xs), y: CUTOFF_V },
          { x: Math.max(...xs), y: CUTOFF_V },
        ],
        borderColor: "#d32f2f",
        borderDash: [6, 4],
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
      });
    }

    // Marker gruppiert nach Typ (Laden/Ausfall/Wechsel).
    const types = Array.from(new Set(markers.map((m) => m.type)));
    for (const type of types) {
      const m = markers.filter((mk) => mk.type === type);
      datasets.push({
        label: m[0].label,
        data: m.map((mk) => ({ x: mk.x, y: mk.y })),
        borderColor: MARKER_COLOR[type] ?? "#888",
        backgroundColor: MARKER_COLOR[type] ?? "#888",
        showLine: false,
        pointRadius: 6,
        pointStyle: "triangle",
        fill: false,
      });
    }

    return { datasets };
  }, [readings, sessions]);

  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: { type: "time" as const, title: { display: true, text: "Zeit" } },
      y: {
        title: { display: true, text: "Spannung (V)" },
      },
    },
    plugins: {
      legend: { display: true },
      tooltip: {
        callbacks: {
          label: (ctx) =>
            `${ctx.dataset.label ?? ""}: ${Number(ctx.parsed.y).toFixed(3)} V`,
        },
      },
    },
  };

  if (readings.length === 0) {
    return (
      <div style={{ color: "#888", fontSize: "1.8vh" }}>
        Keine Akku-Messwerte fuer diesen Sensor.
      </div>
    );
  }

  return (
    <div style={{ height }}>
      <Line data={chartData} options={options} />
    </div>
  );
};
