import React from 'react'
import { expect, vi, describe, it, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('@clerk/react', () => {
  return {
    SignIn: () => null,
    UserButton: () => <div data-testid="userbutton" />,
    useUser: () => ({ isLoaded: true, isSignedIn: true, user: { id: 'test-user', firstName: 'Test' } }),
  }
})

import App from '../App'

describe('Tag UI', () => {
  beforeEach(() => {
    global.fetch = vi.fn((url) => {
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

  it('adds an upload tag when Enter is pressed', async () => {
    render(<App />)

    // Go to Post Video page (nav button is the first match)
    const postButtons = screen.getAllByRole('button', { name: /post video/i })
    const postNav = postButtons[0]
    fireEvent.click(postNav)

    const tagInput = screen.getByPlaceholderText(/Add a tag and press Enter/i)
    fireEvent.change(tagInput, { target: { value: 'near miss' } })
    fireEvent.keyDown(tagInput, { key: 'Enter', code: 'Enter' })

    // tag pill should appear
    const tagPill = await screen.findByText(/near miss/i)
    expect(tagPill).toBeTruthy()
  })

  it('collapses tag list and shows +N more when many tags present', async () => {
    // Mock feed to return a post with many tags
    global.fetch = vi.fn((url) => {
      if (String(url).includes('/api/feed/')) {
        return Promise.resolve({ ok: true, json: async () => ({ items: [
          { id: 'v1', title: 'Many tags', tags: ['one','two','three','four','five'], playback_url: '', owner_clerk_user_id: 'u1', created_at: new Date().toISOString() }
        ] }) })
      }
      if (String(url).includes('/api/auth/me/')) {
        return Promise.resolve({ ok: true, json: async () => ({ is_admin: false }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })

    render(<App />)

    // Feed should show the post and collapsed tags
    const moreToggle = await screen.findByText(/\+2 more|\+\d+ more/i)
    expect(moreToggle).toBeTruthy()
  })
})
