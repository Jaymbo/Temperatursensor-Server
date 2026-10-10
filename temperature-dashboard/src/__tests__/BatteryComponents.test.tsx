import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { BatteryBadge } from '../components/BatteryBadge';
import { StatusTable } from '../components/StatusTable';
import { FleetComparison } from '../components/FleetComparison';
import { relativeToMedian } from '../utils';
import type { BatterySummaryEntry } from '../utils';
import type { FleetEntry } from '../hooks/useBatteryData';

const summaryEntry: BatterySummaryEntry = {
  sensor_id: '3',
  battery_id: 'B-1',
  latest: {
    measured_at: '2026-03-05T10:00:00',
    received_at: '2026-03-05T10:00:01',
    mv: 3800,
  },
  level: { stage: 3, label: 'mittel', under_load: true },
  age_seconds: 120,
  hw_rev: 3,
};

describe('BatteryBadge', () => {
  it('renders sensor, voltage, level (Last) and age', () => {
    const { container, unmount } = render(<BatteryBadge entry={summaryEntry} />);
    const text = container.textContent ?? '';
    expect(text).toContain('#3');
    expect(text).toContain('3.80 V');
    expect(text).toContain('mittel');
    expect(text).toContain('(Last)');
    expect(text).toContain('vor 2 min');
    unmount();
  });

  it('calls onClick with the sensor id', () => {
    const onClick = vi.fn();
    const { container, unmount } = render(
      <BatteryBadge entry={summaryEntry} onClick={onClick} />
    );
    fireEvent.click(container.querySelector('button')!);
    expect(onClick).toHaveBeenCalledWith('3');
    unmount();
  });

  it('renders placeholders when no reading/level present', () => {
    const empty: BatterySummaryEntry = {
      sensor_id: '9',
      battery_id: null,
      latest: null,
      level: { stage: null, label: 'unbekannt', under_load: false },
      age_seconds: null,
      hw_rev: null,
    };
    const { container, unmount } = render(<BatteryBadge entry={empty} />);
    expect(container.textContent).toContain('— V');
    expect(container.textContent).toContain('unbekannt');
    expect(container.querySelector('button')!.getAttribute('title')).toBe(
      'Kein Akku zugeordnet'
    );
    unmount();
  });

  it('compact variant shows only symbol + voltage and stops propagation', () => {
    const onClick = vi.fn();
    const { container, unmount } = render(
      <BatteryBadge entry={summaryEntry} onClick={onClick} compact />
    );
    const text = container.textContent ?? '';
    expect(text).toContain('3.80 V');
    expect(text).not.toContain('#3');
    expect(text).not.toContain('vor 2 min');
    const button = container.querySelector('button')!;
    // stopPropagation: das Event darf nicht bis zur Session-Liste durchreichen.
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    const stopPropagation = vi.spyOn(event, 'stopPropagation');
    button.dispatchEvent(event);
    expect(onClick).toHaveBeenCalledWith('3');
    expect(stopPropagation).toHaveBeenCalled();
    unmount();
  });

  it('compact variant without battery assignment shows "Kein Akku zugeordnet"', () => {
    const empty: BatterySummaryEntry = {
      sensor_id: '9',
      battery_id: null,
      latest: null,
      level: { stage: null, label: 'unbekannt', under_load: false },
      age_seconds: null,
      hw_rev: null,
    };
    const { container, unmount } = render(
      <BatteryBadge entry={empty} onClick={() => {}} compact />
    );
    expect(container.querySelector('button')!.getAttribute('title')).toBe(
      'Kein Akku zugeordnet'
    );
    expect(container.textContent).toContain('— V');
    unmount();
  });
});

const fleetEntry: FleetEntry = {
  sensor_id: '1',
  battery_id: 'B-1',
  hw_rev: 3,
  rate_mv_per_day: -40,
  remaining_days: 12.5,
  cycles: 4,
  latest_mv: 3800,
  latest_at: '2026-03-05T10:00:00',
  level: { stage: 3, label: 'mittel', under_load: false },
  suspicion: { color: 'gelb', cause: 'akku_oder_geraet', reason: '2.0x Median' },
};

describe('StatusTable', () => {
  it('shows an empty hint without entries', () => {
    const { container, unmount } = render(<StatusTable entries={[]} />);
    expect(container.textContent).toContain('Keine Akku-Daten');
    unmount();
  });

  it('renders metrics and suspicion cause', () => {
    const { container, unmount } = render(<StatusTable entries={[fleetEntry]} />);
    const text = container.textContent ?? '';
    expect(text).toContain('#1');
    expect(text).toContain('B-1');
    expect(text).toContain('3.80 V');
    expect(text).toContain('-40.0 mV/Tag');
    expect(text).toContain('12.5 d');
    expect(text).toContain('4');
    expect(text).toContain('akku_oder_geraet');
    unmount();
  });

  it('emits the sensor id on row click', () => {
    const onSelect = vi.fn();
    const { container, unmount } = render(
      <StatusTable entries={[fleetEntry]} onSelect={onSelect} />
    );
    fireEvent.click(container.querySelector('tbody tr')!);
    expect(onSelect).toHaveBeenCalledWith('1');
    unmount();
  });

  it('shows placeholders without latest/suspicion data', () => {
    const bare = {
      ...fleetEntry,
      sensor_id: '7',
      battery_id: null,
      latest_mv: null,
      latest_at: null,
      remaining_days: null,
      cycles: null,
      suspicion: undefined as unknown as FleetEntry['suspicion'],
    };
    const { container, unmount } = render(<StatusTable entries={[bare]} />);
    const text = container.textContent ?? '';
    expect(text).toContain('—');
    expect(text).toContain('unbekannt');
    unmount();
  });
});

describe('FleetComparison', () => {
  it('computes relative-to-median percentage', () => {
    expect(relativeToMedian(-40, -20)).toBe(200);
    expect(relativeToMedian(-10, -20)).toBe(50);
  });

  it('returns null for missing/zero/non-finite median', () => {
    expect(relativeToMedian(null, -20)).toBeNull();
    expect(relativeToMedian(-20, null)).toBeNull();
    expect(relativeToMedian(-20, 0)).toBeNull();
    expect(relativeToMedian(NaN, -20)).toBeNull();
    expect(relativeToMedian(-20, Infinity)).toBeNull();
  });

  it('shows an empty hint without fleet', () => {
    const { container, unmount } = render(
      <FleetComparison fleet={[]} medianRate={null} />
    );
    expect(container.textContent).toContain('Keine Flottendaten');
    unmount();
  });

  it('renders a bar capped at 100% and the median', () => {
    const { container, unmount } = render(
      <FleetComparison fleet={[fleetEntry]} medianRate={-20} />
    );
    expect(container.textContent).toContain('Flotten-Median');
    expect(container.textContent).toContain('-20.0 mV/Tag');
    const bar = container.querySelector('[data-testid="fleet-bar-1"]') as HTMLElement;
    // -40 vs Median -20 -> 200 %, auf 100 % begrenzt
    expect(bar.style.width).toBe('100%');
    unmount();
  });

  it('shows em-dash when rate/median unknown', () => {
    const unknown: FleetEntry = {
      ...fleetEntry,
      sensor_id: '2',
      rate_mv_per_day: null,
    };
    const { container, unmount } = render(
      <FleetComparison fleet={[unknown]} medianRate={null} />
    );
    expect(container.textContent).toContain('—');
    unmount();
  });

  it('tolerates missing suspicion and cycles', () => {
    const bare = {
      ...fleetEntry,
      sensor_id: '5',
      cycles: null,
      suspicion: undefined as unknown as FleetEntry['suspicion'],
    };
    const { container, unmount } = render(
      <FleetComparison fleet={[bare]} medianRate={-20} />
    );
    expect(container.textContent).toContain('#5');
    expect(container.textContent).not.toContain('Zyklen');
    unmount();
  });

  it('emits the sensor id when a row is clicked', () => {
    const onSelect = vi.fn();
    const { container, unmount } = render(
      <FleetComparison fleet={[fleetEntry]} medianRate={-20} onSelect={onSelect} />
    );
    fireEvent.click(container.querySelector('[data-testid="fleet-bar-1"]')!);
    expect(onSelect).toHaveBeenCalledWith('1');
    unmount();
  });
});
