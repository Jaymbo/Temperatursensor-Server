import { describe, it, expect } from 'vitest';
import { getColorForString, yourUtilityFunction, parseCorrectionPoints } from '../utils';

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