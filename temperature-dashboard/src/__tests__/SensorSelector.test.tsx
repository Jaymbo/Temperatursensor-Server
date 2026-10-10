import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { SensorSelector } from '../components/SensorSelector'
import type { BatterySummaryEntry } from '../utils'

describe('SensorSelector', () => {
  const sessions = [
    { sensor_session: '1_10', custom_text: 'Office' },
    { sensor_session: '1_2', custom_text: 'Lab' },
    { sensor_session: 'None_5', custom_text: 'Pending' },
  ]

  it('filters out None_ sessions and sorts by session id desc', () => {
    const onChange = vi.fn()
    const { container, unmount } = render(<SensorSelector sensorSessions={sessions} selected={[]} onChange={onChange} />)
    const options = Array.from(container.querySelectorAll('option')).map(
      (o) => (o as HTMLOptionElement).value
    )
    expect(options).toEqual(['1_10', '1_2'])
    unmount()
  })

  it('search filters by label and custom text', () => {
  const onChange = vi.fn()
    const { container, unmount } = render(
      <SensorSelector sensorSessions={sessions} selected={[]} onChange={onChange} />
    )
    const input = container.querySelector('input[placeholder="Search..."]') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'office' } })
    const options = Array.from(container.querySelectorAll('option')).map((o) => o.textContent)
    expect(options).toEqual(['#10 Office'])
    unmount()
  })

  it('emits selected values on change', () => {
  const onChange = vi.fn()
    const { container, unmount } = render(
      <SensorSelector sensorSessions={sessions} selected={[]} onChange={onChange} />
    )
    const select = container.querySelector('select') as HTMLSelectElement
    // Select first option (#10)
    const option = container.querySelectorAll('option')[0] as HTMLOptionElement
    option.selected = true
    fireEvent.change(select)
    expect(onChange).toHaveBeenCalled()
    const values = onChange.mock.calls[0][0] as string[]
    expect(values).toEqual(['1_10'])
    unmount()
  })

  it('shows one compact battery badge per sensor (latest session) and forwards clicks', () => {
    const onChange = vi.fn()
    const onBatteryClick = vi.fn()
    const summary: BatterySummaryEntry[] = [
      {
        sensor_id: '1',
        battery_id: 'B-1',
        latest: { measured_at: '2026-03-05T10:00:00', received_at: '', mv: 3900 },
        level: { stage: 4, label: 'gut', under_load: false },
        age_seconds: 60,
        hw_rev: 2,
      },
      {
        sensor_id: '2',
        battery_id: null,
        latest: null,
        level: { stage: null, label: 'unbekannt', under_load: false },
        age_seconds: null,
        hw_rev: null,
      },
    ]
    const { container, unmount } = render(
      <SensorSelector
        sensorSessions={sessions}
        selected={[]}
        onChange={onChange}
        batterySummary={summary}
        onBatteryClick={onBatteryClick}
      />
    )
    const badges = Array.from(container.querySelectorAll('button'))
    expect(badges).toHaveLength(1) // nur Sensor '1' hat Daten, Sensor '2' leer
    expect(badges[0].textContent).toContain('3.90 V')
    fireEvent.click(badges[0])
    expect(onBatteryClick).toHaveBeenCalledWith('1')
    unmount()
  })
})
