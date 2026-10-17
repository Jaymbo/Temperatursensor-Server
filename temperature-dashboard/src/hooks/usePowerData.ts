import { useCallback, useState } from "react";
import { buildApiUrl } from "../api";

/**
 * Datenversorgung des Strom-Panels (kontinuierlicher Stream-Modus).
 *
 * Anders als die fruehere Zyklus-Overlay-Ansicht (Trigger + Phasen) zeigt
 * das Strom-Panel jetzt eine durchgehende Strom-Kurve ueber der Absolutezeit:
 *   - /power/stream/{logger}  -> Zeit-Buckets (min/max/mean) + Session-Baender
 *   - /power/charge/{logger}  -> Ladung (Trapez-Integration) fuer einen Bereich
 *   - /power/selections/...   -> gespeicherte Verbrauchs-Bereiche (persistiert)
 *   - /power/window/{logger}  -> definiertes Zoomfenster einer Session
 *
 * Die Auswertung (Integration, Bucket-Bildung, Session-Zuordnung) macht das
 * Backend - hier kommen nur die aufbereiteten Daten an.
 */

/** Ein Zeit-Bucket des Streams (min/max erhalten Spikes, mean traegt Ladung). */
export interface PowerBucket {
  /** Bucket-Start, absolut in Epoch-Millisekunden. */
  t_ms: number;
  ma_min: number;
  ma_max: number;
  ma_mean: number;
  /** Anzahl Rohproben im Bucket (0-Proben-Buckets kommen nicht an). */
  n: number;
}

/** Session-Hintergrundband (Firmware-/Konfigurations-Abschnitt im Stream). */
export interface PowerStreamSession {
  id: number;
  sensor_id: number | null;
  start_time: string;
  /** Ende = Start der naechsten Session; null = offenes Ende (aktuellste). */
  end_time: string | null;
  start_ms: number;
  end_ms: number | null;
  custom_text: string | null;
}

/** Antwort von /power/stream/{logger}. */
export interface PowerStream {
  logger_id: string;
  buckets: PowerBucket[];
  from_ts: string;
  to_ts: string;
  sessions: PowerStreamSession[];
}

/** Ladungs-Auswertung eines Bereichs (/power/charge). */
export interface PowerChargeResult {
  charge_uah: number;
  duration_s: number;
  mean_ma: number;
  n_samples: number;
  /** Anzahl Datenluecken (> 15 ms ohne Probe) im Bereich. */
  gaps: number;
}

/** Gespeicherter Verbrauchs-Bereich (ueber Reloads hinweg persistent). */
export interface PowerSelection {
  id: number;
  label: string | null;
  from_ts: string;
  to_ts: string;
  duration_s: number;
  mean_ma: number;
  charge_uah: number;
  created_at: string;
}

/** Auswaehlbare Session (Auswahl im Strom-Panel). */
export interface PowerSessionOption {
  id: number;
  sensor_id: number | null;
  start_time: string;
  custom_text: string | null;
}

/** Definiertes Zoomfenster einer Session (/power/window). */
export interface PowerWindow {
  session_id: number;
  custom_text: string | null;
  /** Session-Start - 20 s Vorlauf (ISO). */
  start: string;
  /** Letzte Messung + 60 s Nachlauf (ISO); null nur bei Live-Follow. */
  end: string | null;
}

export const usePowerData = () => {
  const [loggers, setLoggers] = useState<string[]>([]);
  const [sessionOptions, setSessionOptions] = useState<PowerSessionOption[]>([]);
  const [stream, setStream] = useState<PowerStream | null>(null);
  const [selections, setSelections] = useState<PowerSelection[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadLoggers = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/power/loggers"));
      if (!res.ok) throw new Error(`Fehler beim Laden der Logger: ${res.statusText}`);
      const data = await res.json();
      setLoggers(data.loggers || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Strom-Logger.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  /** Auswaehlbare Sessions (neueste zuerst). */
  const loadSessionOptions = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/sessions"));
      if (!res.ok) throw new Error(`Fehler beim Laden der Sessions: ${res.statusText}`);
      const data = await res.json();
      const raw: Array<{
        id: number; sensor_id: number | null; start_time: string;
        custom_text: string | null;
      }> = data.sessions || [];
      const opts: PowerSessionOption[] = raw
        .filter((s) => s.start_time != null)
        .map((s) => ({
          id: Number(s.id),
          sensor_id: s.sensor_id ?? null,
          start_time: String(s.start_time),
          custom_text: s.custom_text ?? null,
        }))
        .sort((a: PowerSessionOption, b: PowerSessionOption) =>
          b.start_time.localeCompare(a.start_time));
      setSessionOptions(opts);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Session-Liste.");
    }
  }, []);

  /**
   * Stream-Buckets fuer ein Zeitfenster laden. `fromIso`/`toIso` sind
   * ISO-Zeitstempel; `bucketS` die Bucket-Dauer in Sekunden (Frontend
   * passt sie an den Zoom-Level an: eng gezoomt = feinere Buckets).
   */
  const loadStream = useCallback(async (
    loggerId: string, fromIso: string, toIso: string, bucketS: number,
  ) => {
    setError(null);
    try {
      const params = new URLSearchParams({
        from_ts: fromIso, to_ts: toIso, bucket_s: String(bucketS),
      });
      const res = await fetch(buildApiUrl(`/power/stream/${loggerId}?${params}`));
      if (!res.ok) throw new Error(`Fehler beim Laden des Streams: ${res.statusText}`);
      setStream(await res.json());
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Stromdaten.");
    }
  }, []);

  /** Gespeicherte Verbrauchs-Bereiche laden (neueste zuerst). */
  const loadSelections = useCallback(async (loggerId: string) => {
    setError(null);
    try {
      const res = await fetch(buildApiUrl(`/power/selections/${loggerId}`));
      if (!res.ok) throw new Error(`Fehler beim Laden der Bereiche: ${res.statusText}`);
      const data = await res.json();
      setSelections(data.selections || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der gespeicherten Bereiche.");
    }
  }, []);

  /**
   * Markierten Bereich speichern. Die Auswertung (Ladung/Dauer/Mittelstrom)
   * berechnet das Backend - hier geht nur der Zeitraum + Label raus.
   * @return Die gespeicherte Auswertung (fuer Sofort-Anzeige) oder null.
   */
  const saveSelection = useCallback(async (
    loggerId: string, fromIso: string, toIso: string, label: string | null,
  ): Promise<PowerChargeResult | null> => {
    setError(null);
    try {
      const res = await fetch(buildApiUrl(`/power/selections/${loggerId}`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from_ts: fromIso, to_ts: toIso, label }),
      });
      if (!res.ok) throw new Error(`Fehler beim Speichern: ${res.statusText}`);
      const data = await res.json();
      await loadSelections(loggerId);
      return data as PowerChargeResult;
    } catch (err) {
      console.error(err);
      setError("Fehler beim Speichern des Bereichs.");
      return null;
    }
  }, [loadSelections]);

  /** Gespeicherten Bereich loeschen (Liste laedt neu). */
  const deleteSelection = useCallback(async (loggerId: string, id: number) => {
    setError(null);
    try {
      const res = await fetch(buildApiUrl(`/power/selections/${loggerId}/${id}`),
        { method: "DELETE" });
      if (!res.ok) throw new Error(`Fehler beim Loeschen: ${res.statusText}`);
      await loadSelections(loggerId);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Loeschen des Bereichs.");
    }
  }, [loadSelections]);

  /**
   * Definiertes Zoomfenster einer Session holen (Session-Start - 20 s bis
   * letzte Messung + 60 s). Ohne sessionId: neueste Session (Live-Follow).
   * @return Das Fenster oder null (Fehler/nicht gefunden).
   */
  const loadWindow = useCallback(async (
    loggerId: string, sessionId?: number,
  ): Promise<PowerWindow | null> => {
    setError(null);
    try {
      const q = sessionId != null ? `?session_id=${sessionId}` : "";
      const res = await fetch(buildApiUrl(`/power/window/${loggerId}${q}`));
      if (!res.ok) return null;
      return await res.json() as PowerWindow;
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden des Session-Fensters.");
      return null;
    }
  }, []);

  return {
    loggers, sessionOptions, stream, selections, isLoading, error,
    loadLoggers, loadSessionOptions, loadStream,
    loadSelections, saveSelection, deleteSelection, loadWindow,
  };
};
