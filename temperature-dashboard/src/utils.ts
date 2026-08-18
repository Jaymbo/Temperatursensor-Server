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