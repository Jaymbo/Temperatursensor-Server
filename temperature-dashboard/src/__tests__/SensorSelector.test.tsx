import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { SensorSelector } from '../components/SensorSelector'

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
})
