import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { PowerOverlayChart } from '../components/PowerOverlayChart';
import { PowerPhaseTable } from '../components/PowerPhaseTable';
import { POWER_PHASE_ORDER, type PowerCycle, type PowerPhaseStat } from '../hooks/usePowerData';

const cycle: PowerCycle = {
  start: '2026-10-12T12:00:02',
  duration_s: 2.395,
  peak_ma: 150.0,
  truncated: false,
  gap: false,
  landmarks: {
    wake: { start_s: 0.0 },
    spike_end: { start_s: 0.1 },
    wlan: { start_s: 1.1 },
    send: { start_s: 1.9 },
  },
  phases: {
    wake: { start_s: 0.0, end_s: 0.1, duration_s: 0.1 },
    measure: { start_s: 0.1, end_s: 1.1, duration_s: 1.0 },
    wlan: { start_s: 1.1, end_s: 1.9, duration_s: 0.8 },
    send_tail: { start_s: 1.9, end_s: 2.395, duration_s: 0.495 },
  },
  t_s: [0.0, 0.1, 1.1, 1.9, 2.395],
  ma: [150.0, 150.0, 60.0, 120.0, 0.1],
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
    expect(container.textContent).toContain('Wake-Spike');
    unmount();
  });
});

describe('PowerPhaseTable', () => {
  it('renders stats for known phases', () => {
    const stats: Record<string, PowerPhaseStat> = {
      wlan: { n: 3, min_s: 0.8, median_s: 0.8, max_s: 2.5 },
    };
    const { container, unmount } = render(<PowerPhaseTable stats={stats} />);
    const text = container.textContent ?? '';
    expect(text).toContain('WLAN-Connect');
    expect(text).toContain('0.800');
    expect(text).toContain('2.500');
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
    expect(POWER_PHASE_ORDER).toEqual(['wake', 'measure', 'wlan', 'send_tail']);
  });
});
