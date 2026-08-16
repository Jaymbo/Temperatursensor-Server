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