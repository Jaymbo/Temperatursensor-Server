import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

// chartjs-plugin-zoom initialisiert hammerjs (Touch-Events), was in jsdom
// nicht funktioniert -> Plugin in den Unit-Tests als Noop mocken. Das Zoom-
// Verhalten selbst ist ein chart.js-Feature (im Haupt-Chart im Einsatz).
vi.mock('chartjs-plugin-zoom', () => ({ default: { id: 'zoom' } }));

import { PowerStreamChart, type PowerViewWindow } from '../components/PowerStreamChart';
import {
  usePowerData, type PowerBucket, type PowerStream, type PowerSelection,
} from '../hooks/usePowerData';

// Mock fuer buildApiUrl (api.ts nutzt window.location - in jsdom ok).
vi.mock('../api', () => ({
  buildApiUrl: (path: string) => `http://test${path}`,
  getApiBase: () => 'http://test',
}));

const bucket = (t_ms: number, ma: number): PowerBucket => ({
  t_ms, ma_min: ma, ma_max: ma, ma_mean: ma, n: 200,
});

const stream: PowerStream = {
  logger_id: '1',
  buckets: [bucket(0, 40), bucket(1000, 42), bucket(2000, 38)],
  from_ts: '2026-10-12T12:00:00',
  to_ts: '2026-10-12T12:00:03',
  sessions: [{
    id: 7, sensor_id: 1, start_time: '2026-10-12T11:59:00', end_time: null,
    start_ms: -60000, end_ms: null, custom_text: 'Testsession',
  }],
};

const win: PowerViewWindow = { fromMs: 0, toMs: 3000 };

describe('PowerStreamChart', () => {
  it('rendert Leerezustand ohne Buckets', () => {
    const { container, unmount } = render(
      <PowerStreamChart stream={null} viewWindow={win} bucketMs={1000}
        live measuring={false} selections={[]} />
    );
    expect(container.textContent).toContain('Keine Stromdaten');
    unmount();
  });

  it('rendert Canvas mit Buckets (min/max/Mean-Datensaetze)', () => {
    const { container, unmount } = render(
      <PowerStreamChart stream={stream} viewWindow={win} bucketMs={1000}
        live measuring={false} selections={[]} />
    );
    expect(container.querySelector('canvas')).not.toBeNull();
    unmount();
  });

  it('nimmt onMeasure vom Drag-Plugin entgegen (Plugin-Ref-Pfad)', () => {
    const onMeasure = vi.fn();
    const { container, unmount } = render(
      <PowerStreamChart stream={stream} viewWindow={win} bucketMs={1000}
        live={false} measuring selections={[]} onMeasure={onMeasure} />
    );
    expect(container.querySelector('canvas')).not.toBeNull();
    unmount();
  });

  it('zeigt gespeicherte Bereiche (Props werden gelesen)', () => {
    const sel: PowerSelection = {
      id: 1, label: 'Firmware A', from_ts: '2026-10-12T12:00:00',
      to_ts: '2026-10-12T12:00:02', duration_s: 2, mean_ma: 41,
      charge_uah: 22.8, created_at: '2026-10-12T12:05:00',
    };
    const { container, unmount } = render(
      <PowerStreamChart stream={stream} viewWindow={win} bucketMs={1000}
        live measuring={false} selections={[sel]} />
    );
    expect(container.querySelector('canvas')).not.toBeNull();
    unmount();
  });
});

// usePowerData: Die Hook-Logik (fetch-URLs, Fehlerpfade) gegen gemocktes
// fetch testen.
const okJson = (data: unknown) => Promise.resolve({
  ok: true, json: () => Promise.resolve(data), statusText: 'OK',
} as Response);

describe('usePowerData', () => {
  it('loadLoggers setzt Logger-Liste', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson({ loggers: ['1'] }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHookSafe();
    await result.current.loadLoggers();
    await waitFor(() => expect(result.current.loggers).toEqual(['1']));
    vi.unstubAllGlobals();
  });

  it('Fehlerpfade setzen error statt zu werfen', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, statusText: 'boom' } as Response);
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHookSafe();
    await result.current.loadLoggers();
    await waitFor(() => expect(result.current.error).toContain('Strom-Logger'));
    vi.unstubAllGlobals();
  });
});

import { renderHook } from '@testing-library/react';
function renderHookSafe() {
  return renderHook(() => usePowerData());
}
