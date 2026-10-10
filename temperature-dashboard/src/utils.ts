export function getColorForString(sensor: string): string {
    let hash = 0;
    const prime = 171; // Ein hoher Primzahlwert für stärkere Streuung
    for (let i = 0; i < sensor.length; i++) {
        hash = sensor.charCodeAt(i)*prime + ((hash << 360) - hash); // Kleinere Verschiebung für bessere Streuung
    }
    hash = hash ^ (hash >>> 16); // XOR für zusätzliche Streuung
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue}, 70%, 50%)`;
}
export function yourUtilityFunction(): string {
    return 'expected result';
}

// Korrekturpunkt: t = gemessene Temperatur, delta = Soll - Ist
export interface CorrectionPoint {
    t: number;
    delta: number;
}

// Naive lokale ISO-String (ohne 'Z'/'UTC'), passend zu den in der DB
// gespeicherten Zeitpunkten (die ebenfalls naive lokale Zeit verwenden).
// Wird für die Zeit-Kalibrierung gebraucht, damit Frontend und Backend
// dieselbe Zeitbasis haben.
export function localIso(date: Date): string {
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

// Baut aus dem (angezeigten) gemessenen Zeitpunkt und einer eingegebenen
// Zeit "HH:mm" (oder "HH:mm:ss") den "wirklichen" Zeitpunkt im gleichen
// Datum wie der gemessene Punkt. Gibt null zurück bei ungültiger Eingabe.
// Beispiel: "das war eigentlich 8:35 statt 7:40" (gleicher Tag).
export function buildActualTimestamp(measured: Date, timeStr: string): Date | null {
    const m = timeStr.trim().match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (!m) return null;
    const h = parseInt(m[1], 10);
    const min = parseInt(m[2], 10);
    const sec = m[3] ? parseInt(m[3], 10) : 0;
    if (h > 23 || min > 59 || sec > 59) return null;
    const d = new Date(measured);
    d.setHours(h, min, sec, 0);
    return d;
}

// Parst den correction_points-Wert aus der DB (JSON-String, z.B.
// '[{"t": 25.0, "delta": -2.0}]') zu einem Array. Ungültige Einträge
// (fehlende/ungültige Zahlen) werden verworfen; ungültiger Gesamtwert -> [].
export function parseCorrectionPoints(raw: unknown): CorrectionPoint[] {
    if (typeof raw !== 'string' || raw.trim() === '') {
        return [];
    }
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return [];
    }
    if (!Array.isArray(parsed)) {
        return [];
    }
    return parsed.filter(
        (p): p is CorrectionPoint =>
            typeof p === 'object' && p !== null &&
            typeof (p as Record<string, unknown>).t === 'number' &&
            Number.isFinite((p as Record<string, unknown>).t as number) &&
            typeof (p as Record<string, unknown>).delta === 'number' &&
            Number.isFinite((p as Record<string, unknown>).delta as number)
    );
}

// =============================================================================
// Akku-Telemetrie (Anzeige-Helfer)
// =============================================================================
//
// Wichtig: Es gibt BEWUSST keine Prozentanzeige. LiPo ist zwischen ca. 3,7 und
// 3,9 V sehr flach - eine "Prozent"-Zahl aus der Spannung waere gelogen. Das
// Backend liefert deshalb 5 STUFEN (siehe backend/battery_health.py).

/** Eine Akku-Stufe (0..5) inkl. Kennzeichnung "unter Last". */
export interface BatteryLevel {
    stage: number | null;
    label: string;
    under_load: boolean;
}

/** Ein einzelner Akku-Messwert (Geraete- und Serverzeit). */
export interface BatteryReading {
    measured_at: string;
    received_at: string;
    mv: number;
    raw?: number | null;
    under_load?: boolean | null;
    seq?: number | null;
    hw_rev?: number | null;
    battery_id?: string | null;
}

/** Statuszeile eines Sensors aus /battery/summary. */
export interface BatterySummaryEntry {
    sensor_id: string;
    battery_id: string | null;
    latest: BatteryReading | null;
    level: BatteryLevel;
    age_seconds: number | null;
    hw_rev: number | null;
}

/**
 * Formatiert ein Alter in Sekunden menschenlesbar (deutsch, kompakt).
 * Unbekanntes/ungueltiges Alter -> "unbekannt".
 */
export function formatAge(seconds: number | null | undefined): string {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) {
        return 'unbekannt';
    }
    if (seconds < 60) return 'gerade eben';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `vor ${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `vor ${hours} h`;
    const days = Math.floor(hours / 24);
    return `vor ${days} d`;
}

/**
 * Wandelt eine Akku-Stufe (0..5) in eine Ampelfarbe um.
 * 0/1 -> rot (kritisch), 2/3 -> gelb (mittel), 4/5 -> gruen (gut).
 * Unbekannt (null) -> grau.
 */
export function levelColor(stage: number | null | undefined): string {
    if (stage == null) return '#9e9e9e';
    if (stage <= 1) return '#d32f2f';
    if (stage <= 3) return '#f9a825';
    return '#2e7d32';
}

/**
 * Alter einer Messung in Sekunden, relativ zu einem Bezugszeitpunkt.
 * Bevorzugt die Geraetezeit (measured_at), faellt auf received_at zurueck.
 * Ungueltige Werte -> null (NICHT raten).
 */
export function readingAgeSeconds(
    reading: { measured_at?: string; received_at?: string } | null | undefined,
    now: number = Date.now()
): number | null {
    if (!reading) return null;
    const raw = reading.measured_at || reading.received_at;
    if (!raw) return null;
    const t = Date.parse(raw);
    if (Number.isNaN(t)) return null;
    return Math.max(0, (now - t) / 1000);
}

/**
 * Wandelt eine Verdachts-Farbe des Backends ("gruen"/"gelb"/"rot"/...) in
 * einen CSS-Farbwert um. Unbekannt -> grau.
 */
export function suspicionColor(color: string | null | undefined): string {
    switch (color) {
        case 'gruen': return '#2e7d32';
        case 'gelb': return '#f9a825';
        case 'rot': return '#d32f2f';
        default: return '#9e9e9e';
    }
}

/**
 * Ein Punkt im Akku-Chart: Zeit (Unix-ms) und Spannung in Volt.
 * 'under_load' kennzeichnet Messungen waehrend einer Lastphase.
 */
export interface BatteryChartPoint {
    x: number;
    y: number;
    under_load: boolean;
}

/**
 * Baut aus Akku-Messwerten die Chart-Punkte (Volt ueber Zeit).
 * Punkte ohne gueltige Zeit/Spannung werden verworfen (NICHT raten).
 */
export function buildBatterySeries(readings: BatteryReading[]): BatteryChartPoint[] {
    const out: BatteryChartPoint[] = [];
    for (const r of readings) {
        const raw = r.measured_at || r.received_at;
        const x = raw ? Date.parse(raw) : NaN;
        const mv = r.mv;
        if (!Number.isFinite(x) || typeof mv !== 'number' || !Number.isFinite(mv)) {
            continue;
        }
        out.push({ x, y: mv / 1000, under_load: !!r.under_load });
    }
    return out;
}

/**
 * Ein Marker im Akku-Chart (Ladevorgang, Ausfall oder Akkuwechsel).
 * 'x' ist der Zeitpunkt (Unix-ms), 'y' die Spannung des naechsten Messpunkts.
 */
export interface BatteryMarker {
    x: number;
    y: number;
    type: string;
    label: string;
}

/**
 * Baut Marker aus den abgeleiteten Betriebsphasen. Die Spannung wird vom
 * zeitlich naechsten Messpunkt uebernommen, damit der Marker auf der Kurve sitzt.
 * Phasen ohne gueltige Startzeit werden verworfen.
 */
export function buildBatteryMarkers(
    sessions: { start_time: string; end_time?: string | null; type: string; note?: string | null }[],
    points: BatteryChartPoint[]
): BatteryMarker[] {
    const markers: BatteryMarker[] = [];
    for (const s of sessions) {
        const x = Date.parse(s.start_time);
        if (!Number.isFinite(x)) continue;
        markers.push({
            x,
            y: nearestValue(points, x),
            type: s.type,
            label: markerLabel(s),
        });
    }
    return markers;
}

/** Spannung des zeitlich naechsten Punkts; 0 wenn keine Punkte vorliegen. */
function nearestValue(points: BatteryChartPoint[], x: number): number {
    if (points.length === 0) return 0;
    let best = points[0];
    let bestDist = Math.abs(best.x - x);
    for (const p of points) {
        const d = Math.abs(p.x - x);
        if (d < bestDist) {
            best = p;
            bestDist = d;
        }
    }
    return best.y;
}

/** Menschenlesbares Label eines Markers (deutsch). */
function markerLabel(s: { type: string; note?: string | null }): string {
    if (s.type === 'charge') return 'Laden';
    if (s.type === 'outage') return 'Ausfall';
    if (s.type === 'swap') return 'Akkuwechsel';
    return s.type;
}

/**
 * Entladerate relativ zum Flotten-Median in Prozent (Betrag).
 * Der Median entspricht 100 %. null, wenn Rate/Median fehlen oder 0 sind.
 *
 * Warum relativ? Ein absoluter Schwellwert kann "alt" nicht von "leer"
 * unterscheiden - erst der Flottenvergleich ist aussagekraeftig.
 */
export function relativeToMedian(
    rate: number | null | undefined,
    median: number | null | undefined
): number | null {
    if (rate == null || median == null) return null;
    if (!Number.isFinite(rate) || !Number.isFinite(median)) return null;
    if (median === 0) return null;
    return (Math.abs(rate) / Math.abs(median)) * 100;
}

/**
 * Ermittelt die neueste Session je Sensor (hoechste session_id).
 * Session-Format: '{sensor_id}_{session_id}'. Sessions ohne '_' oder ohne
 * parsebare ID werden ignoriert; 'None_'-Pending-Sessions ebenfalls (dort ist
 * der Sensor noch nicht gestartet -> kein Akku-Badge).
 */
export function latestSessionPerSensor(
    sessions: { sensor_session: string }[]
): Record<string, string> {
    const out: Record<string, string> = {};
    for (const s of sessions) {
        const session = s.sensor_session || "";
        if (session.startsWith("None_")) continue;
        const parts = session.split("_");
        if (parts.length !== 2) continue;
        const num = parseInt(parts[1], 10);
        if (Number.isNaN(num)) continue;
        const sid = parts[0];
        if (!out[sid]) {
            out[sid] = session;
            continue;
        }
        const cur = parseInt(out[sid].split("_")[1], 10);
        if (Number.isNaN(cur) || num > cur) out[sid] = session;
    }
    return out;
}