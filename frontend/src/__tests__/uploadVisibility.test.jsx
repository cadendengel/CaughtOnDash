import { expect, vi, describe, it, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@clerk/react', () => ({
  SignIn: () => null,
  UserButton: () => <div data-testid="userbutton" />,
  useUser: () => ({ isLoaded: true, isSignedIn: true, user: { id: 'test-user', firstName: 'Test' } }),
  useAuth: () => ({ getToken: async () => 'test-session-token' }),
}))

import App from '../App'

describe('Upload visibility', () => {
  let bodies
  let srcDescriptor

  beforeEach(() => {
    bodies = []
    globalThis.fetch = vi.fn((url, options = {}) => {
      if (String(url).includes('/api/videos/upload-url/')) {
        bodies.push(JSON.parse(options.body))
        return Promise.resolve({ ok: true, json: async () => ({ video: { id: 'v1' } }) })
      }
      if (String(url).includes('/api/feed/')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
    // jsdom cannot read a video file. Report it unreadable at once, which the
    // form treats as "no duration, no poster" and carries on.
    srcDescriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src')
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      configurable: true,
      set() { setTimeout(() => this.onerror && this.onerror()) },
      get() { return '' },
    })
    URL.createObjectURL = vi.fn(() => 'blob:test')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    if (srcDescriptor) Object.defineProperty(HTMLMediaElement.prototype, 'src', srcDescriptor)
    vi.restoreAllMocks()
  })

  const openForm = () => {
    render(<App />)
    fireEvent.click(screen.getAllByRole('button', { name: /post video/i })[0])
  }

  it('offers the three visibilities, public by default', () => {
    openForm()
    const choices = screen.getAllByRole('radio')
    expect(choices.map((c) => c.value)).toEqual(['public', 'unlisted', 'private'])
    expect(screen.getByRole('radio', { name: /^Public/ }).checked).toBe(true)
  })

  it('sends the chosen visibility when the upload starts', async () => {
    openForm()
    fireEvent.click(screen.getByRole('radio', { name: /^Private/ }))
    const fileInput = document.querySelector('input[type="file"]')
    fireEvent.change(fileInput, { target: { files: [new File(['x'], 'dash.mov', { type: 'video/quicktime' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

    await waitFor(() => expect(bodies.length).toBe(1))
    expect(bodies[0].visibility).toBe('private')
  })
})
