import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

// chartjs-plugin-zoom initialisiert hammerjs (Touch-Events), was in jsdom
// nicht funktioniert -> Plugin in den Unit-Tests als Noop mocken. Das Zoom-
// Verhalten selbst ist ein chart.js-Feature (im Haupt-Chart im Einsatz).
vi.mock('chartjs-plugin-zoom', () => ({ default: { id: 'zoom' } }));

import { PowerOverlayChart } from '../components/PowerOverlayChart';
import { PowerPhaseTable } from '../components/PowerPhaseTable';
import { POWER_PHASE_ORDER, type PowerCycle, type PowerPhaseStat } from '../hooks/usePowerData';

const cycle: PowerCycle = {
  start: '2026-10-12T12:00:02',
  duration_s: 10.0,
  peak_ma: 135.0,
  truncated: false,
  gap: false,
  landmarks: {
    trigger: { start_s: 5.0 },
    spike_end: { start_s: 6.0 },
  },
  phases: {
    pre_peak: { start_s: 0.0, end_s: 5.0, duration_s: 5.0 },
    spike: { start_s: 5.0, end_s: 6.0, duration_s: 1.0 },
    tail: { start_s: 6.0, end_s: 10.0, duration_s: 4.0 },
  },
  t_s: [0.0, 5.0, 6.0, 10.0],
  ma: [74.5, 135.0, 74.5, 74.5],
};

describe('PowerOverlayChart', () => {
  it('renders empty-state without cycles', () => {
    const { container, unmount } = render(<PowerOverlayChart cycles={[]} />);
    expect(container.textContent).toContain('Keine Zyklen erkannt');
    unmount();
  });

  it('renders cycle with canvas and phase legend', () => {
    const { container, unmount } = render(<PowerOverlayChart cycles={[cycle]} />);
    expect(container.querySelector('canvas')).not.toBeNull();
    expect(container.textContent).toContain('TX-Burst');
    unmount();
  });
});

describe('PowerPhaseTable', () => {
  it('renders stats for known phases', () => {
    const stats: Record<string, PowerPhaseStat> = {
      spike: { n: 3, min_s: 1.0, median_s: 1.0, max_s: 2.7 },
    };
    const { container, unmount } = render(<PowerPhaseTable stats={stats} />);
    const text = container.textContent ?? '';
    expect(text).toContain('TX-Burst');
    expect(text).toContain('1.000');
    expect(text).toContain('2.700');
    unmount();
  });

  it('renders empty-state without stats', () => {
    const { container, unmount } = render(<PowerPhaseTable stats={{}} />);
    expect(container.textContent).toContain('Keine Phasen-Statistik');
    unmount();
  });
});

describe('usePowerData constants', () => {
  it('phase order matches backend definition', () => {
    expect(POWER_PHASE_ORDER).toEqual(['pre_peak', 'spike', 'tail']);
  });
});
