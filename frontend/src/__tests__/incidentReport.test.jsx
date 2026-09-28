import { expect, vi, describe, it } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'

import IncidentReport from '../IncidentReport'
import { LOOKUPS } from '../incidentHelpers'

// Shaped like the 2026-09-26 case: one moment with its overlay reading, one
// photo whose plate came back "S39 SCA" with the fourth character in doubt.
const REPORT = {
  kind: 'incident-report',
  video_id: 'v1',
  evidence: {
    sha256: 'a'.repeat(64),
    original_filename: '2026_0926_121621_2947.MOV',
    custody_status: 'verified',
    provenance: { hints: [{ code: 'apple_export', message: 'Exported through an iPhone, not the original.' }] },
    overlay: {
      available: true,
      clock: { start: '2026-09-26T12:16:19' },
      track: [{ t_seconds: 0.5, speed: 79, speed_unit: 'mph' }, { t_seconds: 48.7, speed: 104, speed_unit: 'mph' }],
    },
    moments: {
      available: true,
      signals: { audio: 'measured', jolt: 'measured' },
      moments: [{
        t_seconds: 26.1,
        score: 1.0,
        reasons: ['sharp sound at 26.1s', 'both signals agree within a second'],
        overlay: { clock: '2026-09-26T12:16:45', speed: 80, speed_unit: 'mph', lat: 30.228611, lon: -97.619722 },
      }],
      possible: [{ t_seconds: 15.7, score: 0.58 }],
    },
  },
  report: { notes: 'Truck cut into my lane.' },
  other_party: { insurer: 'Acme Mutual' },
  artifacts: [
    { id: 'b1', kind: 'burst', url: 'https://signed/burst.jpg', t_seconds: 26.1, attempt_number: 2, label: 'Moment 1 burst' },
    { id: 'old', kind: 'burst', url: 'https://signed/old.jpg', t_seconds: 26.1, attempt_number: 1, label: 'Stale burst' },
    { id: 'cs', kind: 'contact_sheet', url: 'https://signed/sheet.jpg', attempt_number: 2 },
    {
      id: 'p1', kind: 'photo', url: 'https://signed/p1.preview.jpg', original_filename: 'IMG_3110.HEIC', sha256: 'b'.repeat(64),
      metadata: {
        exif: { taken_at: '2026-09-26T12:19:32-05:00', focal_length_35mm: 177,
          device: { make: 'Apple', model: 'iPhone 17 Pro' },
          gps: { lat: 30.2790167, lon: -97.5839472, accuracy_m: 2, heading_deg: 47.2, heading_ref: 'true', speed_kmh: 36 } },
        text: {
          available: true,
          plates: [{ text: 'S39 SCA', reads: 11, uncertain: [{ index: 3, read: 'S', could_be: ['5', '8', '9'] }] }],
          identifiers: [
            { kind: 'domain', value: 'cotrucks.com', may_be_truncated: true },
            { kind: 'usdot', value: '1234567', lookup: 'https://safer.fmcsa.dot.gov/query.asp?query_string=1234567' },
          ],
          legible: ['RENT-A-TRUCK', 'COMMERCIAL DUTY'],
        },
      },
    },
    { id: 'c1', kind: 'plate_crop', url: 'https://signed/crop.jpg', attempt_number: 2, label: 'Plate candidate: S39 SCA',
      metadata: { source_artifact_id: 'p1' } },
  ],
  access_log: [{ clerk_user_id: 'user_owner', action: 'view', as_admin: false, at: '2026-09-27T10:00:00Z' }],
}

const makeFetch = (overrides = {}) => {
  const calls = []
  const authFetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    const key = `${options.method || 'GET'} ${url.replace('http://api/api/videos/v1', '')}`
    const handler = overrides[key]
    if (handler) return handler(options)
    return { ok: true, json: async () => REPORT, blob: async () => new Blob(['zip']) }
  })
  return { authFetch, calls }
}

const renderReport = (fetchOverrides) => {
  const fetch = makeFetch(fetchOverrides)
  render(<IncidentReport videoId="v1" videoTitle="I-35 cut-in" apiBase="http://api" authFetch={fetch.authFetch} onBack={() => {}} />)
  return fetch
}

describe('IncidentReport', () => {
  it('shows each moment with the dashcam clock, speed and place', async () => {
    renderReport()
    const moment = await screen.findByTestId('moment')
    expect(within(moment).getByText('Moment 1 · 0:26.1')).toBeTruthy()
    expect(within(moment).getByText('2026-09-26 12:16:45')).toBeTruthy()
    expect(within(moment).getByText('80 mph')).toBeTruthy()
    expect(within(moment).getByText(/30\.22861°N 97\.61972°W/)).toBeTruthy()
    expect(within(moment).getByText('both signals agree within a second')).toBeTruthy()
    expect(screen.getByText(/Worth a glance/).closest('p').textContent).toContain('0:15.7 (0.58)')
    expect(screen.getByText('Exported through an iPhone, not the original.')).toBeTruthy()
  })

  it('shows only the latest attempt\'s images', async () => {
    renderReport()
    await screen.findByTestId('moment')
    expect(screen.getByAltText('Moment 1 burst')).toBeTruthy()
    expect(screen.queryByAltText('Stale burst')).toBeNull()
  })

  it('marks the doubtful plate character with its alternatives', async () => {
    renderReport()
    const plate = await screen.findByTestId('plate-reading')
    expect(plate.textContent).toBe('S39 SCA')
    const doubtful = within(plate).getByTitle('Read as S; could be 5, 8, 9')
    expect(doubtful.textContent).toBe('S')
    expect(screen.getByAltText('Plate candidate: S39 SCA')).toBeTruthy()   // the crop, to check against
  })

  it('reads the photo metadata and links carrier numbers to FMCSA', async () => {
    renderReport()
    const photo = await screen.findByTestId('photo')
    expect(within(photo).getByText('Apple iPhone 17 Pro, 177 mm equivalent')).toBeTruthy()
    expect(within(photo).getByText('47.2° true')).toBeTruthy()
    expect(within(photo).getByText('(may be cut off)')).toBeTruthy()
    expect(within(photo).getByText('FMCSA record').getAttribute('href')).toContain('1234567')
  })

  it('saves the other party and notes', async () => {
    const { calls } = renderReport()
    const insurer = await screen.findByLabelText('Insurer')
    expect(insurer.value).toBe('Acme Mutual')
    fireEvent.change(screen.getByLabelText('Claim number'), { target: { value: 'CLM-42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await screen.findByText('Saved.')
    const patch = calls.find((c) => c.method === 'PATCH')
    const body = JSON.parse(patch.body)
    expect(body.other_party.claim_number).toBe('CLM-42')
    expect(body.other_party.insurer).toBe('Acme Mutual')
    expect(body.notes).toBe('Truck cut into my lane.')
  })

  it('uploads photos and then offers to read them', async () => {
    const { calls } = renderReport()
    await screen.findByTestId('photo')
    const input = document.querySelector('input[type="file"]')
    fireEvent.change(input, { target: { files: [new File(['x'], 'IMG_1.HEIC', { type: 'image/heic' })] } })
    await screen.findByText(/text on the vehicle is read at the next analysis/)
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/incident/photos/'))).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Read text on photos' }))
    await screen.findByText(/Analysis requested/)
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/analyze/'))).toBe(true)
  })

  it('says why when the report cannot be loaded', async () => {
    renderReport({ 'GET /incident/': async () => ({ ok: false, json: async () => ({ detail: 'Not found.' }) }) })
    expect(await screen.findByText('Not found.')).toBeTruthy()
    expect(screen.getByText('Incident report unavailable')).toBeTruthy()
  })

  it('offers vehicle and carrier lookups only', async () => {
    renderReport()
    await screen.findByTestId('moment')
    const urls = LOOKUPS.flatMap((group) => group.links.map(([, url]) => url)).join(' ')
    expect(urls).not.toMatch(/pimeyes|receipt|highprogrammer/i)
    expect(screen.getByText('FMCSA record for USDOT 1234567')).toBeTruthy()
  })
})
