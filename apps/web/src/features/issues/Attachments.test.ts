import { describe, expect, it } from 'vitest'

import { formatBytes } from './Attachments'

describe('formatBytes', () => {
  it('uses bytes below 1 KiB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
  })

  it('steps up through the units', () => {
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
    expect(formatBytes(1024 * 1024 * 1024)).toBe('1.0 GB')
  })

  it('drops the decimal once the number is big enough to read', () => {
    expect(formatBytes(1024 * 15)).toBe('15 KB')
    expect(formatBytes(1024 * 1024 * 250)).toBe('250 MB')
  })

  it('stops at GB rather than inventing units', () => {
    expect(formatBytes(1024 ** 4)).toBe('1024 GB')
  })
})
