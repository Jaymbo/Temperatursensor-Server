import { useCallback, useEffect, useState } from "react";
import { buildApiUrl } from "../api";
import type { BatterySummaryEntry, BatteryReading } from "../utils";

/**
 * Laedt die Akku-Telemetrie vom Server.
 *
 * Bewusst getrennt von useSensorData: Akku-Daten haben eine andere Zeitbasis
 * (Geraete-Elapsed) und eine andere Aktualisierungsrate als Temperaturdaten.
 * Die Messwerte kommen zusammen mit dem Temperatur-Paket an, daher wird nach
 * einem WebSocket-Update gezielt neu geladen (siehe refresh).
 */

export interface BatterySessionMarker {
  start_time: string;
  end_time?: string | null;
  type: string;
  note?: string | null;
}

export interface BatteryHistory {
  sensor_id: string;
  readings: BatteryReading[];
  sessions: BatterySessionMarker[];
  assignment: { battery_id: string; from_time: string; to_time: string | null } | null;
}

export interface FleetEntry {
  sensor_id: string;
  battery_id: string | null;
  hw_rev: number | null;
  rate_mv_per_day: number | null;
  remaining_days: number | null;
  cycles: number | null;
  latest_mv: number | null;
  latest_at: string | null;
  level: { stage: number | null; label: string; under_load: boolean };
  suspicion: { color: string; cause: string; reason: string };
}

export const useBatteryData = () => {
  const [summary, setSummary] = useState<BatterySummaryEntry[]>([]);
  const [fleet, setFleet] = useState<FleetEntry[]>([]);
  const [medianRate, setMedianRate] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/battery/summary"));
      if (!res.ok) throw new Error(`Fehler beim Laden der Akku-Uebersicht: ${res.statusText}`);
      const data = await res.json();
      setSummary(data.summary || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Akku-Uebersicht.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadFleet = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/battery/fleet"));
      if (!res.ok) throw new Error(`Fehler beim Laden des Flottenvergleichs: ${res.statusText}`);
      const data = await res.json();
      setFleet(data.fleet || []);
      setMedianRate(data.median_rate_mv_per_day ?? null);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden des Flottenvergleichs.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  /** Verlauf eines Sensors (fuer den Akku-Chart). */
  const loadHistory = useCallback(async (sensorId: string): Promise<BatteryHistory | null> => {
    try {
      const res = await fetch(buildApiUrl(`/battery/${encodeURIComponent(sensorId)}`));
      if (!res.ok) throw new Error(`Fehler beim Laden des Akku-Verlaufs: ${res.statusText}`);
      return (await res.json()) as BatteryHistory;
    } catch (err) {
      console.error(err);
      return null;
    }
  }, []);

  // Beim Start einmal laden.
  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  return {
    summary,
    fleet,
    medianRate,
    isLoading,
    error,
    loadSummary,
    loadFleet,
    loadHistory,
  };
};

/**
 * Laedt den Verlauf fuer mehrere Sensoren parallel (Akku-Chart).
 * Fehlende/unbekannte Sensoren werden ausgelassen.
 */
export async function loadHistories(
  sensorIds: string[]
): Promise<Record<string, BatteryHistory>> {
  const entries = await Promise.all(
    sensorIds.map(async (id) => {
      try {
        const res = await fetch(buildApiUrl(`/battery/${encodeURIComponent(id)}`));
        if (!res.ok) return null;
        return [id, (await res.json()) as BatteryHistory] as const;
      } catch {
        return null;
      }
    })
  );
  const out: Record<string, BatteryHistory> = {};
  for (const e of entries) {
    if (e) out[e[0]] = e[1];
  }
  return out;
}
