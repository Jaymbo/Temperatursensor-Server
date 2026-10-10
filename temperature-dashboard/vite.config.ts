import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      reportsDirectory: 'coverage',
      all: true,
      include: ['src/**/*.{ts,tsx}'],
      // Exclude heavy integration and UI wiring files from unit coverage
      exclude: [
        'src/__tests__/**',
        'src/components/Chart.tsx',
        // BatteryChart ist wie Chart.tsx ein reiner chart.js-Wrapper; die
        // Aufbereitung der Serien/Marker ist in utils.ts (100 % getestet).
        'src/components/BatteryChart.tsx',
        'src/App.tsx',
        'src/main.tsx',
        'src/hooks/**',
      ],
    },
  },
  server: {
    watch: {
      usePolling: true,
    },
    host: true,
    strictPort: true,
    port: 5173,
  },
})
