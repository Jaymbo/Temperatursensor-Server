import { describe, it, expect } from 'vitest'
import { getColorForString } from '../utils'

describe('smoke util', () => {
  it('hashes a string to a color', () => {
    const color = getColorForString('sensor-x')
    expect(color).toMatch(/^hsl\(\d{1,3}, 70%, 50%\)$/)
  })
})
