import React from 'react'
import { expect, vi, describe, it, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// Mock Clerk react hooks/components used by App
vi.mock('@clerk/react', () => {
  return {
    SignIn: () => null,
    UserButton: () => <div data-testid="userbutton" />,
    useUser: () => ({ isLoaded: true, isSignedIn: true, user: { id: 'test-user', firstName: 'Test', emailAddresses: [{ emailAddress: 'test@example.com' }], username: 'tester', imageUrl: '' } }),
  }
})

// Render App after mocking
import App from '../App'

describe('Search UI', () => {
  beforeEach(() => {
    // Simple fetch mock that returns different payloads based on URL
    global.fetch = vi.fn((url) => {
      if (String(url).includes('/api/videos/search')) {
        return Promise.resolve({ ok: true, json: async () => ({ payload: { items: [ { id: 'vid-1', title: 'Brake Check Near Miss', playback_url: '', tags: [], owner_clerk_user_id: 'u1', created_at: new Date().toISOString() } ] } }) })
      }
      if (String(url).includes('/api/feed/')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) })
      }
      if (String(url).includes('/api/auth/me/')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_admin: false }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders search page and displays results from the API', async () => {
    render(<App />)

    // Open Search tab
    const searchNav = screen.getByRole('button', { name: /search/i })
    fireEvent.click(searchNav)

    // Ensure search input is present
    const input = screen.getByPlaceholderText(/freeway merge, near miss, brake check/i)
    fireEvent.change(input, { target: { value: 'brake' } })

    const buttons = screen.getAllByRole('button', { name: /search/i })
    // first is nav, second is the form submit
    const searchButton = buttons[1]
    fireEvent.click(searchButton)

    // Wait for async results to appear
    await waitFor(() => expect(global.fetch).toHaveBeenCalled())

    const resultTitle = await screen.findByText(/Brake Check Near Miss/i)
    expect(resultTitle).toBeTruthy()
  })

  it('clear button resets the input and shows the empty search card', async () => {
    render(<App />)

    // Open Search tab
    const searchNav = screen.getByRole('button', { name: /search/i })
    fireEvent.click(searchNav)

    const input = screen.getByPlaceholderText(/freeway merge, near miss, brake check/i)
    fireEvent.change(input, { target: { value: 'brake' } })

    const buttons = screen.getAllByRole('button', { name: /search/i })
    const searchButton = buttons[1]
    fireEvent.click(searchButton)

    await waitFor(() => expect(global.fetch).toHaveBeenCalled())
    const resultTitle = await screen.findByText(/Brake Check Near Miss/i)
    expect(resultTitle).toBeTruthy()

    // Click Clear and assert reset
    const clearButton = screen.getByRole('button', { name: /clear/i })
    fireEvent.click(clearButton)

    // input should be cleared
    expect(input.value).toBe('')

    // empty search card should be visible
    const emptyHeading = await screen.findByText(/Search the video catalog/i)
    expect(emptyHeading).toBeTruthy()
  })
})
