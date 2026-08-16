import { describe, it, expect } from 'vitest';
import { getColorForString, yourUtilityFunction } from '../utils';

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