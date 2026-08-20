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