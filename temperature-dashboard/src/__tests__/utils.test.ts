import { describe, it, expect } from 'vitest';
import {
  getColorForString, yourUtilityFunction, parseCorrectionPoints,
  localIso, buildActualTimestamp,
  formatAge, levelColor, readingAgeSeconds, suspicionColor,
  buildBatterySeries, buildBatteryMarkers,
  latestSessionPerSensor,
} from '../utils';

describe('Utility Function Tests', () => {
    it('should return expected result from yourUtilityFunction', () => {
        expect(yourUtilityFunction()).toBe('expected result');
    });

    it('should generate consistent colors for the same string', () => {
        const color1 = getColorForString('sensor1');
        const color2 = getColorForString('sensor1');
        expect(color1).toBe(color2);
    });

        it('should generate different colors for different strings', () => {
        const color1 = getColorForString('sensor1');
        const color2 = getColorForString('sensor2');
        expect(color1).not.toBe(color2);
    });
});

describe('parseCorrectionPoints', () => {
    it('parses a valid JSON correction-point string', () => {
        const raw = '[{"t": 25.0, "delta": -2.0}, {"t": 50.0, "delta": 1.5}]';
        expect(parseCorrectionPoints(raw)).toEqual([
            { t: 25.0, delta: -2.0 },
            { t: 50.0, delta: 1.5 },
        ]);
    });

    it('returns empty array for null/undefined/empty values (DB ohne Kalibrierung)', () => {
        expect(parseCorrectionPoints(null)).toEqual([]);
        expect(parseCorrectionPoints(undefined)).toEqual([]);
        expect(parseCorrectionPoints('')).toEqual([]);
        expect(parseCorrectionPoints('   ')).toEqual([]);
    });

    it('returns empty array for invalid JSON', () => {
        expect(parseCorrectionPoints('not-json')).toEqual([]);
    });

    it('returns empty array when JSON is not an array', () => {
        expect(parseCorrectionPoints('{"t": 1}')).toEqual([]);
        expect(parseCorrectionPoints('42')).toEqual([]);
    });

    it('filters out entries with non-finite or missing numbers', () => {
        const raw = '[{"t": 10, "delta": 1}, {"t": "x", "delta": 1}, {"delta": 2}]';
        expect(parseCorrectionPoints(raw)).toEqual([{ t: 10, delta: 1 }]);
    });
});

describe('localIso (Zeit-Kalibrierung)', () => {
    it('formats a Date as naive local ISO string (no Z, no UTC)', () => {
        // Naive lokale Zeit: 2026-03-05 08:35:07
        const d = new Date(2026, 2, 5, 8, 35, 7);
        expect(localIso(d)).toBe('2026-03-05T08:35:07');
    });

    it('zero-pads single-digit fields', () => {
        const d = new Date(2026, 0, 5, 3, 7, 9);
        expect(localIso(d)).toBe('2026-01-05T03:07:09');
    });
});

describe('buildActualTimestamp (Zeit-Kalibrierung)', () => {
    const measured = new Date(2026, 2, 5, 7, 40, 0); // 05.03.2026 07:40

    it('keeps the same date and applies the requested HH:mm', () => {
        // "das war eigentlich 8:35 statt 7:40" -> gleicher Tag, 08:35
        const actual = buildActualTimestamp(measured, '08:35');
        expect(actual).not.toBeNull();
        expect(actual!.getFullYear()).toBe(2026);
        expect(actual!.getMonth()).toBe(2);
        expect(actual!.getDate()).toBe(5);
        expect(actual!.getHours()).toBe(8);
        expect(actual!.getMinutes()).toBe(35);
        expect(actual!.getSeconds()).toBe(0);
    });

    it('accepts HH:mm:ss', () => {
        const actual = buildActualTimestamp(measured, '08:35:30');
        expect(actual!.getHours()).toBe(8);
        expect(actual!.getMinutes()).toBe(35);
        expect(actual!.getSeconds()).toBe(30);
    });

    it('returns null for invalid input', () => {
        expect(buildActualTimestamp(measured, '')).toBeNull();
        expect(buildActualTimestamp(measured, 'abc')).toBeNull();
        expect(buildActualTimestamp(measured, '24:00')).toBeNull();  // Stunde > 23
        expect(buildActualTimestamp(measured, '08:60')).toBeNull();  // Minute > 59
        expect(buildActualTimestamp(measured, '08:00:60')).toBeNull(); // Sekunde > 59
    });
});

describe('formatAge (Akku-Alter)', () => {
    it('returns "unbekannt" for null/negative/non-finite values', () => {
        expect(formatAge(null)).toBe('unbekannt');
        expect(formatAge(undefined)).toBe('unbekannt');
        expect(formatAge(-5)).toBe('unbekannt');
        expect(formatAge(NaN)).toBe('unbekannt');
    });

    it('formats seconds, minutes, hours and days', () => {
        expect(formatAge(30)).toBe('gerade eben');
        expect(formatAge(120)).toBe('vor 2 min');
        expect(formatAge(3 * 3600)).toBe('vor 3 h');
        expect(formatAge(2 * 86400)).toBe('vor 2 d');
    });
});

describe('levelColor (Akku-Ampel)', () => {
    it('maps stages to red/yellow/green and null to grey', () => {
        expect(levelColor(0)).toBe('#d32f2f');
        expect(levelColor(1)).toBe('#d32f2f');
        expect(levelColor(2)).toBe('#f9a825');
        expect(levelColor(3)).toBe('#f9a825');
        expect(levelColor(4)).toBe('#2e7d32');
        expect(levelColor(5)).toBe('#2e7d32');
        expect(levelColor(null)).toBe('#9e9e9e');
        expect(levelColor(undefined)).toBe('#9e9e9e');
    });
});

describe('suspicionColor (Verdachtsfarbe)', () => {
    it('maps backend colors to CSS colors', () => {
        expect(suspicionColor('gruen')).toBe('#2e7d32');
        expect(suspicionColor('gelb')).toBe('#f9a825');
        expect(suspicionColor('rot')).toBe('#d32f2f');
        expect(suspicionColor('unbekannt')).toBe('#9e9e9e');
        expect(suspicionColor(null)).toBe('#9e9e9e');
    });
});

describe('readingAgeSeconds', () => {
    const now = Date.parse('2026-03-05T10:00:00');

    it('prefers measured_at and falls back to received_at', () => {
        expect(readingAgeSeconds({ measured_at: '2026-03-05T09:59:00' }, now)).toBe(60);
        expect(readingAgeSeconds({ received_at: '2026-03-05T09:58:00' }, now)).toBe(120);
    });

    it('returns null for missing/invalid input and clamps to >= 0', () => {
        expect(readingAgeSeconds(null)).toBeNull();
        expect(readingAgeSeconds({})).toBeNull();
        expect(readingAgeSeconds({ measured_at: 'not-a-date' })).toBeNull();
        // Zukunft -> 0 statt negativ
        expect(readingAgeSeconds({ measured_at: '2026-03-05T10:01:00' }, now)).toBe(0);
    });
});

describe('buildBatterySeries (Akku-Chart)', () => {
    it('converts mV to V and skips invalid points', () => {
        const series = buildBatterySeries([
            { measured_at: '2026-03-05T10:00:00', received_at: '', mv: 3800 },
            { measured_at: 'not-a-date', received_at: '', mv: 3900 },
            { measured_at: '2026-03-05T10:01:00', received_at: '', mv: 3750, under_load: true },
        ]);
        expect(series).toHaveLength(2);
        expect(series[0]).toEqual({
            x: Date.parse('2026-03-05T10:00:00'), y: 3.8, under_load: false,
        });
        expect(series[1].y).toBe(3.75);
        expect(series[1].under_load).toBe(true);
    });

    it('falls back to received_at when measured_at is missing', () => {
        const series = buildBatterySeries([
            { measured_at: '', received_at: '2026-03-05T10:00:00', mv: 3800 },
        ]);
        expect(series).toHaveLength(1);
    });

    it('skips a reading with no timestamp at all', () => {
        const series = buildBatterySeries([
            { measured_at: '', received_at: '', mv: 3800 },
        ]);
        expect(series).toEqual([]);
    });
});

describe('buildBatteryMarkers (Akku-Chart)', () => {
    const points = [
        { x: Date.parse('2026-03-05T10:00:00'), y: 3.8, under_load: false },
        { x: Date.parse('2026-03-05T11:00:00'), y: 3.7, under_load: false },
    ];

    it('labels charge/outage/swap and picks nearest y', () => {
        const markers = buildBatteryMarkers([
            { start_time: '2026-03-05T10:05:00', type: 'charge' },
            { start_time: '2026-03-05T10:59:00', type: 'outage' },
            { start_time: '2026-03-05T10:00:00', type: 'swap' },
        ], points);
        expect(markers.map((m) => m.label)).toEqual(['Laden', 'Ausfall', 'Akkuwechsel']);
        expect(markers[0].y).toBe(3.8);
        expect(markers[1].y).toBe(3.7);
    });

    it('skips markers with invalid start_time and uses 0 without points', () => {
        const markers = buildBatteryMarkers([
            { start_time: 'bad', type: 'charge' },
            { start_time: '2026-03-05T10:00:00', type: 'measure' },
        ], []);
        expect(markers).toHaveLength(1);
        expect(markers[0].label).toBe('measure');
        expect(markers[0].y).toBe(0);
    });
});

describe('latestSessionPerSensor (Session-Liste: Akku-Badge je Sensor)', () => {
    it('returns the highest session id per sensor', () => {
        const out = latestSessionPerSensor([
            { sensor_session: '1_2' },
            { sensor_session: '1_10' },
            { sensor_session: '2_3' },
        ]);
        expect(out).toEqual({ '1': '1_10', '2': '2_3' });
    });

    it('ignores None_ pending sessions and unparseable entries', () => {
        const out = latestSessionPerSensor([
            { sensor_session: 'None_5' },
            { sensor_session: 'kaputt' },
            { sensor_session: '1_x' },
            { sensor_session: '1_4' },
        ]);
        expect(out).toEqual({ '1': '1_4' });
    });

    it('ignores empty session strings', () => {
        const out = latestSessionPerSensor([
            { sensor_session: '' },
            { sensor_session: '1_4' },
        ]);
        expect(out).toEqual({ '1': '1_4' });
    });

    it('returns an empty object for an empty list', () => {
        expect(latestSessionPerSensor([])).toEqual({});
    });
});