# Temperature Dashboard (React + TypeScript + Vite)

Web-Oberfläche des Temperatur-Monitorings: Sensor-Sessions, Live-/Verlaufs-Charts,
Kalibrierung und seit Kurzem die **Akku-Telemetrie**.

## Akku-Ansicht

Der Toolbar-Button **„Akku“** öffnet ein Panel (`aria-label="Akku-Uebersicht"`).
Es lädt beim Öffnen Flotten- und Summary-Daten und aktualisiert sie über den
WebSocket-Kanal (`new_measurements`).

### Komponenten (`src/components/`)

| Datei | Zweck |
|---|---|
| `BatteryBadge.tsx` | Button je Sensor: Ampelpunkt, `#sensor_id`, Spannung, Stufe (+„(Last)“), Alter |
| `StatusTable.tsx` | Tabelle: Ampel/Spannung/Stufe/Entladerate/Resttage/Zyklen/Verdacht (mit Ursache + `title=reason`) |
| `FleetComparison.tsx` | normierte Balken (Median = 100 %, auf 100 % gedeckelt) |
| `BatteryChart.tsx` | Chart.js-Linie: Spannung über Zeit, `CUTOFF_V=3.15`-Referenz, Marker nach Typ, `under_load` hervorgehoben |

Klick auf Badge, Tabellenzeile oder Balken lädt den Verlauf des Sensors in den Chart.

### Daten-Hook (`src/hooks/useBatteryData.ts`)

`useBatteryData()` liefert `summary`, `fleet`, `medianRate`, `isLoading`, `error`
sowie `loadSummary`, `loadFleet`, `loadHistory`. Zusätzlich exportiert:
`loadHistories(sensorIds)`.

### Helfer (`src/utils.ts`)

`formatAge`, `levelColor`, `readingAgeSeconds`, `suspicionColor`,
`buildBatterySeries`, `buildBatteryMarkers`, `relativeToMedian` – inkl. Typen
`BatteryLevel`, `BatteryReading`, `BatterySummaryEntry`, `BatteryChartPoint`,
`BatteryMarker`.

### Anzeige-Konventionen

- **5 Stufen statt Prozent** (`levelColor`: ≤1 rot, ≤3 gelb, sonst grün) – eine
  lineare Prozentanzeige aus der LiPo-Spannung gibt es nicht.
- Spannung **unter Last** wird gekennzeichnet, nicht umskaliert.
- Verdacht wird über `suspicionColor` (gruen/gelb/rot, sonst grau) eingefärbt.

### Tests

```bash
npm test              # Vitest (jsdom)
npm run coverage      # mit Coverage
npx tsc -b            # Type-Check
npm run build         # Type-Check + Production-Build
```

- `src/__tests__/utils.test.ts` – Akku-Helfer
- `src/__tests__/BatteryComponents.test.tsx` – Badge, StatusTable, FleetComparison
- `BatteryChart.tsx` ist (wie `Chart.tsx`) von der Coverage ausgenommen
  (`vite.config.ts` → `coverage.exclude`).

## React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default tseslint.config({
  extends: [
    // Remove ...tseslint.configs.recommended and replace with this
    ...tseslint.configs.recommendedTypeChecked,
    // Alternatively, use this for stricter rules
    ...tseslint.configs.strictTypeChecked,
    // Optionally, add this for stylistic rules
    ...tseslint.configs.stylisticTypeChecked,
  ],
  languageOptions: {
    // other options...
    parserOptions: {
      project: ['./tsconfig.node.json', './tsconfig.app.json'],
      tsconfigRootDir: import.meta.dirname,
    },
  },
})
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default tseslint.config({
  plugins: {
    // Add the react-x and react-dom plugins
    'react-x': reactX,
    'react-dom': reactDom,
  },
  rules: {
    // other rules...
    // Enable its recommended typescript rules
    ...reactX.configs['recommended-typescript'].rules,
    ...reactDom.configs.recommended.rules,
  },
})
```
