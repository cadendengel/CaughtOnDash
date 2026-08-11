import React from 'react'
import { expect, vi, describe, it, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'

vi.mock('@clerk/react', () => {
  return {
    SignIn: () => null,
    UserButton: () => <div data-testid="userbutton" />,
    useUser: () => ({
      isLoaded: true,
      isSignedIn: true,
      user: {
        id: 'test-admin',
        firstName: 'Test',
        emailAddresses: [{ emailAddress: 'admin@example.com' }],
        username: 'admin',
        imageUrl: '',
      },
    }),
    useAuth: () => ({ getToken: async () => 'test-session-token' }),
  }
})

import App from '../App'

// Deliberately out of order, and chosen so a naive sort gets each column wrong:
// alphabetical state would put Running last, and an empty analyzer version
// would sort first.
const ITEMS = [
  {
    video_id: 'v-done',
    title: 'Bravo highway',
    state: 'done',
    state_label: 'Done',
    analyzer_version: 'detect-2.0',
    attempt_number: 2,
    duration_seconds: 143,
    analysis_progress: 0,
    created_at: '2026-01-02T00:00:00Z',
    last_result: { attempt_number: 2, status: 'complete', summary: 'Detected car, traffic sign.' },
  },
  {
    video_id: 'v-new',
    title: 'Alpha backroad',
    state: 'not_started',
    state_label: 'Not started',
    analyzer_version: '',
    attempt_number: 0,
    duration_seconds: 30,
    analysis_progress: 0,
    created_at: '2026-01-03T00:00:00Z',
    last_result: null,
  },
  {
    video_id: 'v-running',
    title: 'Charlie underpass',
    state: 'running',
    state_label: 'Running',
    analyzer_version: 'detect-1.0',
    attempt_number: 1,
    duration_seconds: 600,
    analysis_progress: 45,
    created_at: '2026-01-01T00:00:00Z',
    last_result: { attempt_number: 1, status: 'failed', error: 'ffmpeg exploded' },
  },
]

const rowTitles = () => {
  const table = screen.getByRole('table')
  return within(table)
    .getAllByRole('row')
    .slice(1) // drop the header row
    .map((row) => row.querySelectorAll('td')[0].textContent)
}

// findByRole retries until the table is there, which is what makes this safe:
// the click kicks off two loads and the assertions run only once the rendered
// result exists.
//
// This emits "not wrapped in act(...)" warnings. Wrapping the click in an async
// act() is the usual answer and it does not work here -- App runs background
// polling, so act() waits for work that never settles and every test times out.
// The warnings come from that polling, not from the assertions below.
const openAdmin = async () => {
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /^admin$/i }))
  await screen.findByRole('table')
}

describe('All videos table', () => {
  beforeEach(() => {
    global.fetch = vi.fn((url) => {
      const target = String(url)
      if (target.includes('/api/videos/admin/all/')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            count: ITEMS.length,
            total: ITEMS.length,
            truncated: false,
            limit: 2000,
            items: ITEMS,
          }),
        })
      }
      if (target.includes('/api/auth/me/')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_admin: true }) })
      }
      if (target.includes('/api/videos/admin/moderation/')) {
        return Promise.resolve({ ok: true, json: async () => ({ counts: { total: 0 }, groups: {} }) })
      }
      if (target.includes('/api/feed/')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders every video with its derived state', async () => {
    await openAdmin()

    expect(rowTitles().sort()).toEqual(['Alpha backroad', 'Bravo highway', 'Charlie underpass'])
    expect(screen.getByText('Not started')).toBeTruthy()
    expect(screen.getByText('Done')).toBeTruthy()
  })

  it('shows progress on a running video only', async () => {
    await openAdmin()

    expect(screen.getByText(/Running\s*45%/)).toBeTruthy()
    expect(screen.queryByText(/Done\s*\d+%/)).toBeNull()
  })

  it('defaults to newest first', async () => {
    await openAdmin()

    expect(rowTitles()).toEqual(['Alpha backroad', 'Bravo highway', 'Charlie underpass'])
  })

  it('sorts by title, and reverses on a second click', async () => {
    await openAdmin()
    const titleHeader = screen.getByRole('button', { name: /title/i })

    fireEvent.click(titleHeader)
    expect(rowTitles()).toEqual(['Alpha backroad', 'Bravo highway', 'Charlie underpass'])

    fireEvent.click(titleHeader)
    expect(rowTitles()).toEqual(['Charlie underpass', 'Bravo highway', 'Alpha backroad'])
  })

  it('sorts state by pipeline order, not alphabetically', async () => {
    await openAdmin()

    fireEvent.click(screen.getByRole('button', { name: /state/i }))

    // Alphabetically this would be Done, Not started, Running.
    expect(rowTitles()).toEqual(['Charlie underpass', 'Alpha backroad', 'Bravo highway'])
  })

  it('sorts never-analyzed videos last by analyzer version', async () => {
    await openAdmin()

    fireEvent.click(screen.getByRole('button', { name: /analyzer/i }))

    // '' would sort first as a plain string; it belongs at the bottom.
    expect(rowTitles()).toEqual(['Charlie underpass', 'Bravo highway', 'Alpha backroad'])
  })

  it('sorts length numerically', async () => {
    await openAdmin()

    fireEvent.click(screen.getByRole('button', { name: /length/i }))

    expect(rowTitles()).toEqual(['Alpha backroad', 'Bravo highway', 'Charlie underpass'])
  })

  it('describes the last result, including failures and never-analyzed', async () => {
    await openAdmin()

    expect(screen.getByText(/Attempt 2: Detected car, traffic sign\./)).toBeTruthy()
    expect(screen.getByText(/Attempt 1 failed: ffmpeg exploded/)).toBeTruthy()
    expect(screen.getByText('Never analyzed')).toBeTruthy()
  })

  it('marks the sorted column for assistive tech', async () => {
    await openAdmin()

    fireEvent.click(screen.getByRole('button', { name: /title/i }))

    const header = screen.getByRole('columnheader', { name: /title/i })
    expect(header.getAttribute('aria-sort')).toBe('ascending')
  })

  it('says so when the list was truncated rather than showing a partial list silently', async () => {
    global.fetch = vi.fn((url) => {
      const target = String(url)
      if (target.includes('/api/videos/admin/all/')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ count: 2, total: 5000, truncated: true, limit: 2000, items: ITEMS.slice(0, 2) }),
        })
      }
      if (target.includes('/api/auth/me/')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_admin: true }) })
      }
      if (target.includes('/api/videos/admin/moderation/')) {
        return Promise.resolve({ ok: true, json: async () => ({ counts: { total: 0 }, groups: {} }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: [] }) })
    })

    await openAdmin()

    expect(screen.getByText(/Showing the first 2 of 5000/)).toBeTruthy()
  })

  it('surfaces a load failure instead of an empty table', async () => {
    global.fetch = vi.fn((url) => {
      const target = String(url)
      if (target.includes('/api/videos/admin/all/')) {
        return Promise.resolve({ ok: false, status: 500, json: async () => ({}) })
      }
      if (target.includes('/api/auth/me/')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_admin: true }) })
      }
      if (target.includes('/api/videos/admin/moderation/')) {
        return Promise.resolve({ ok: true, json: async () => ({ counts: { total: 0 }, groups: {} }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: [] }) })
    })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /^admin$/i }))

    await waitFor(() => expect(screen.getByText(/Could not load the video list/)).toBeTruthy())
  })
})
