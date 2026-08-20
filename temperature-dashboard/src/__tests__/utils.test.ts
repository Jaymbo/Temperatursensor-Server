import { describe, it, expect } from 'vitest';
import {
  getColorForString, yourUtilityFunction, parseCorrectionPoints,
  localIso, buildActualTimestamp,
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