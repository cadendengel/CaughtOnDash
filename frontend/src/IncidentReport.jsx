import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ACTION_ROW,
  CARD,
  EYEBROW,
  FORM_ACTIONS,
  FORM_MESSAGE_ERROR,
  FORM_MESSAGE_SUCCESS,
  GHOST_BTN,
  PAGE_CONTENT,
  PAGE_HEADING,
  PRIMARY_BTN,
  SECONDARY_BTN,
} from './ui'
import {
  LOOKUPS,
  OTHER_PARTY_FIELDS,
  formatClock,
  formatPlace,
  formatSeconds,
  latestAttempt,
  mapLinks,
} from './incidentHelpers'
import PlateReading from './PlateReading'

// The incident report: everything the analysis found about one video that is
// too sensitive for the public page -- when and where it happened, how fast,
// what the other vehicle said on it, and who the other driver is. Owner and
// admins only; the backend enforces that and logs every read.
//
// Everything automated is shown as a reading with its reasons, never as fact:
// the plate on the case photo came back "S39 SCA" against a true "S39 9CA".

const SECTION = `${CARD} p-5 max-[640px]:p-4`
const SECTION_TITLE = 'font-heading text-xl text-ink'
const SECTION_NOTE = 'mt-1 max-w-[70ch] text-[0.9rem] text-muted'
const FACTS = 'mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[0.92rem] ' +
  '[&>dt]:font-semibold [&>dt]:text-muted [&>dd]:min-w-0 [&>dd]:break-words [&>dd]:text-ink'
const MONO = 'font-mono text-[0.82rem] break-all'
const THUMB = 'block w-full rounded-panel border border-ink/10 bg-ink/[0.04] object-contain'
const FIELD = 'grid gap-1 text-[0.88rem] font-semibold text-ink'
const INPUT = 'w-full rounded-control border border-ink/15 bg-white/90 px-3 py-2 font-normal text-ink'
const BADGE = 'inline-flex items-center rounded-full px-2.5 py-0.5 text-[0.78rem] font-bold'
const LINK = 'font-semibold text-brand underline decoration-brand/30 underline-offset-2 hover:decoration-brand'

const CUSTODY = {
  verified: {
    tone: 'bg-green-600/10 text-good',
    label: 'Verified',
    note: 'The worker analyzed exactly the bytes that were uploaded.',
  },
  mismatch: {
    tone: 'bg-red-600/10 text-bad',
    label: 'Mismatch',
    note: 'The worker analyzed a different file from the one uploaded. Treat the analysis with care.',
  },
  unverified: {
    tone: 'bg-ink/[0.06] text-muted',
    label: 'Not yet verified',
    note: 'No worker has confirmed the file it analyzed matches the upload.',
  },
}

const Image = ({ artifact, alt }) =>
  artifact?.url ? (
    <a href={artifact.url} target="_blank" rel="noreferrer" title="Open full size">
      <img src={artifact.url} alt={alt} loading="lazy" className={THUMB} />
    </a>
  ) : (
    <div className={`${THUMB} grid min-h-[120px] place-items-center text-[0.85rem] text-muted`}>
      Image unavailable
    </div>
  )

function IncidentReport({ videoId, videoTitle, apiBase, authFetch, onBack }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState('')
  const [notes, setNotes] = useState('')
  const [otherParty, setOtherParty] = useState({})
  const [showLog, setShowLog] = useState(false)
  const fileInput = useRef(null)
  const base = `${apiBase}/api/videos/${videoId}`

  const applyReport = useCallback((data) => {
    setReport(data)
    setNotes(data?.report?.notes || '')
    setOtherParty(data?.other_party || {})
  }, [])

  const fetchReport = useCallback(async () => {
    const response = await authFetch(`${base}/incident/`)
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail || 'The incident report could not be loaded.')
    }
    return data
  }, [authFetch, base])

  // A refresh after a change: the page stays up while it reloads.
  const load = useCallback(async () => {
    applyReport(await fetchReport())
  }, [fetchReport, applyReport])

  useEffect(() => {
    let cancelled = false
    fetchReport()
      .then((data) => { if (!cancelled) applyReport(data) })
      .catch((err) => { if (!cancelled) setError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [fetchReport, applyReport])

  const run = async (label, action) => {
    setBusy(label)
    setError('')
    setMessage('')
    try {
      await action()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const save = () =>
    run('save', async () => {
      const fields = Object.fromEntries(OTHER_PARTY_FIELDS.map(([key]) => [key, otherParty[key] || '']))
      const response = await authFetch(`${base}/incident/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes, other_party: fields }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not save.')
      applyReport(data)
      setMessage('Saved.')
    })

  const uploadPhotos = (files) =>
    run('upload', async () => {
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        const response = await authFetch(`${base}/incident/photos/`, { method: 'POST', body: form })
        const data = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(`${file.name}: ${data.detail || 'upload failed.'}`)
      }
      await load()
      setMessage(
        `${files.length === 1 ? 'Photo' : 'Photos'} added. Location and time were read now; ` +
          'text on the vehicle is read at the next analysis -- use "Read text on photos".',
      )
      if (fileInput.current) fileInput.current.value = ''
    })

  const readPhotos = () =>
    run('read', async () => {
      const response = await authFetch(`${base}/analyze/`, { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not request analysis.')
      setMessage('Analysis requested. Once it is approved and a worker has run it, the text will appear here.')
    })

  const exportPackage = () =>
    run('export', async () => {
      const response = await authFetch(`${base}/incident/export/`)
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.detail || 'Could not build the evidence package.')
      }
      const blob = await response.blob()
      if (typeof URL.createObjectURL !== 'function') return
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `incident-${videoId}.zip`
      link.click()
      URL.revokeObjectURL(link.href)
    })

  if (loading) {
    return (
      <section className={PAGE_CONTENT}>
        <div className={PAGE_HEADING}><h2>Loading incident report…</h2></div>
      </section>
    )
  }

  if (!report) {
    return (
      <section className={PAGE_CONTENT}>
        <div className={PAGE_HEADING}><h2>Incident report unavailable</h2></div>
        {error ? <p className={FORM_MESSAGE_ERROR}>{error}</p> : null}
        <div className={FORM_ACTIONS}>
          <button type="button" className={SECONDARY_BTN} onClick={onBack}>Back to video</button>
        </div>
      </section>
    )
  }

  const evidence = report.evidence || {}
  const artifacts = report.artifacts || []
  const attempt = latestAttempt(artifacts)
  const current = artifacts.filter((a) => a.kind !== 'photo' && (attempt == null || a.attempt_number === attempt))
  const photos = artifacts.filter((a) => a.kind === 'photo')
  const moments = evidence.moments || {}
  const overlay = evidence.overlay || {}
  const custody = CUSTODY[evidence.custody_status] || CUSTODY.unverified
  const speeds = (overlay.track || []).filter((p) => p.speed != null)
  const identifiers = photos.flatMap((p) => p.metadata?.text?.identifiers || [])
  const carrierLinks = identifiers.filter((i) => i.lookup)
  const firstPlace = (moments.moments || []).map((m) => m.overlay).find((o) => o?.lat != null)

  const imagesFor = (moment) =>
    current.filter((a) => ['burst', 'frame', 'vehicle_crop'].includes(a.kind) && a.t_seconds != null &&
      Math.abs(a.t_seconds - moment.t_seconds) <= 2.5)

  const renderMoment = (moment, number) => {
    const seen = moment.overlay || {}
    const place = formatPlace(seen.lat, seen.lon)
    const images = imagesFor(moment)
    const burst = images.find((a) => a.kind === 'burst')
    const others = images.filter((a) => a !== burst)
    return (
      <article key={`${moment.t_seconds}`} className="mt-4 grid gap-3 border-t border-ink/10 pt-4" data-testid="moment">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h4 className="font-heading text-lg text-ink">Moment {number} · {formatSeconds(moment.t_seconds)}</h4>
          <span className={`${BADGE} bg-ink/[0.06] text-ink`}>score {moment.score}</span>
        </div>
        <dl className={FACTS}>
          <dt>Dashcam clock</dt><dd>{formatClock(seen.clock) || 'not read'}</dd>
          <dt>Speed</dt><dd>{seen.speed != null ? `${seen.speed} ${seen.speed_unit}` : 'not read'}</dd>
          <dt>Position</dt>
          <dd>
            {place ? (
              <>
                {place}{' '}
                {mapLinks(seen.lat, seen.lon).map(([label, url]) => (
                  <a key={label} className={`${LINK} ml-2`} href={url} target="_blank" rel="noreferrer">{label}</a>
                ))}
              </>
            ) : 'not read'}
          </dd>
          {moment.closest_vehicle ? (
            <>
              <dt>Closest vehicle</dt>
              <dd>
                {moment.closest_vehicle.label} at {formatSeconds(moment.closest_vehicle.t_seconds)}, filling{' '}
                {Math.round(moment.closest_vehicle.largest_frame_share * 100)}% of the frame
              </dd>
            </>
          ) : null}
        </dl>
        <ul className="list-disc pl-5 text-[0.9rem] text-body">
          {(moment.reasons || []).map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
        {burst ? <Image artifact={burst} alt={burst.label || `Frames around moment ${number}`} /> : null}
        {others.length ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2">
            {others.map((a) => <Image key={a.id} artifact={a} alt={a.label || a.kind} />)}
          </div>
        ) : null}
      </article>
    )
  }

  const renderPhoto = (photo) => {
    const exif = photo.metadata?.exif || {}
    const gps = exif.gps || {}
    const device = exif.device || {}
    const text = photo.metadata?.text
    const place = formatPlace(gps.lat, gps.lon)
    const crops = current.filter((a) => a.kind === 'plate_crop' && a.metadata?.source_artifact_id === photo.id)
    return (
      <article key={photo.id} className="mt-4 grid gap-4 border-t border-ink/10 pt-4 md:grid-cols-[minmax(0,280px)_1fr]" data-testid="photo">
        <Image artifact={photo} alt={photo.original_filename || 'Photo'} />
        <div className="min-w-0">
          <h4 className="font-semibold text-ink">{photo.original_filename || 'Photo'}</h4>
          <dl className={FACTS}>
            <dt>Taken</dt><dd>{exif.taken_at ? formatClock(exif.taken_at) : 'no time in file'}</dd>
            <dt>Position</dt>
            <dd>
              {place ? (
                <>
                  {place}{gps.accuracy_m != null ? ` ±${gps.accuracy_m} m` : ''}
                  {mapLinks(gps.lat, gps.lon).slice(0, 1).map(([label, url]) => (
                    <a key={label} className={`${LINK} ml-2`} href={url} target="_blank" rel="noreferrer">{label}</a>
                  ))}
                </>
              ) : 'no location in file'}
            </dd>
            {gps.heading_deg != null ? <><dt>Facing</dt><dd>{gps.heading_deg}° {gps.heading_ref}</dd></> : null}
            {gps.speed_kmh != null ? <><dt>Moving</dt><dd>{gps.speed_kmh} km/h</dd></> : null}
            {device.model ? (
              <>
                <dt>Camera</dt>
                <dd>{[device.make, device.model].filter(Boolean).join(' ')}
                  {exif.focal_length_35mm ? `, ${exif.focal_length_35mm} mm equivalent` : ''}</dd>
              </>
            ) : null}
            <dt>SHA-256</dt><dd className={MONO}>{photo.sha256}</dd>
          </dl>

          {!text ? (
            <p className={SECTION_NOTE}>Text on this photo has not been read yet.</p>
          ) : text.available === false ? (
            <p className={SECTION_NOTE}>Text could not be read: {text.reason}</p>
          ) : (
            <div className="mt-3 grid gap-2">
              {(text.plates || []).map((plate) => (
                <div key={plate.text} className="grid gap-1" data-testid="plate">
                  <span className={EYEBROW}>Plate candidate</span>
                  <PlateReading plate={plate} />
                  {(plate.uncertain || []).length ? (
                    <span className="text-[0.82rem] text-muted">
                      Highlighted characters are uncertain -- hover for alternatives, and check the crop.
                    </span>
                  ) : null}
                </div>
              ))}
              {crops.length ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-2">
                  {crops.map((c) => <Image key={c.id} artifact={c} alt={c.label || 'Plate crop'} />)}
                </div>
              ) : null}
              {(text.identifiers || []).length ? (
                <ul className="grid gap-1 text-[0.92rem]">
                  {text.identifiers.map((found) => (
                    <li key={`${found.kind}-${found.value}`}>
                      <span className="font-semibold text-muted">{found.kind}</span>{' '}
                      <span className="font-mono">{found.value}</span>
                      {found.may_be_truncated ? <span className="text-muted"> (may be cut off)</span> : null}
                      {found.lookup ? (
                        <a className={`${LINK} ml-2`} href={found.lookup} target="_blank" rel="noreferrer">FMCSA record</a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {(text.legible || []).length ? (
                <p className="text-[0.88rem] text-body">
                  <span className="font-semibold text-muted">Also read: </span>{text.legible.join(' · ')}
                </p>
              ) : null}
            </div>
          )}
        </div>
      </article>
    )
  }

  return (
    <section className={`${PAGE_CONTENT} mx-auto mt-6 max-w-[960px] px-4 max-[640px]:px-3`}>
      <div className={PAGE_HEADING}>
        <span className={EYEBROW}>Private · visible to you and admins only</span>
        <h2>Incident report</h2>
        <p>{videoTitle}</p>
      </div>

      <div className={ACTION_ROW}>
        <button type="button" className={SECONDARY_BTN} onClick={onBack}>Back to video</button>
        <button type="button" className={PRIMARY_BTN} onClick={exportPackage} disabled={busy === 'export'}>
          {busy === 'export' ? 'Building package…' : 'Download evidence package'}
        </button>
      </div>

      {error ? <p className={FORM_MESSAGE_ERROR} role="alert">{error}</p> : null}
      {message ? <p className={FORM_MESSAGE_SUCCESS} role="status">{message}</p> : null}

      <section className={SECTION}>
        <h3 className={SECTION_TITLE}>The file</h3>
        <dl className={FACTS}>
          <dt>Uploaded</dt><dd>{evidence.original_filename || '--'}</dd>
          <dt>SHA-256</dt><dd className={MONO}>{evidence.sha256 || 'not recorded (uploaded before fingerprinting)'}</dd>
          <dt>Analysis</dt>
          <dd><span className={`${BADGE} ${custody.tone}`}>{custody.label}</span> <span className="text-muted">{custody.note}</span></dd>
        </dl>
        {(evidence.provenance?.hints || []).map((hint) => (
          <p key={hint.code} className="mt-3 rounded-control bg-amber-400/15 px-4 py-3 text-[0.92rem] text-warn">{hint.message}</p>
        ))}
      </section>

      <section className={SECTION}>
        <h3 className={SECTION_TITLE}>What happened, when</h3>
        <p className={SECTION_NOTE}>
          Candidate moments come from a sharp sound and a camera jolt; the clock, speed and position are read off the
          dashcam's own overlay. These are readings to check against the frames, not findings.
        </p>
        {overlay.clock ? (
          <p className="mt-2 text-[0.92rem] text-body">
            Dashcam clock at the start of the video: <strong>{formatClock(overlay.clock.start)}</strong>
            {speeds.length ? ` · speed ${Math.min(...speeds.map((p) => p.speed))}–${Math.max(...speeds.map((p) => p.speed))} ${speeds[0].speed_unit}` : ''}
          </p>
        ) : null}
        {(moments.moments || []).length ? (
          moments.moments.map((moment, i) => renderMoment(moment, i + 1))
        ) : (
          <p className="mt-3 text-[0.92rem] text-body">
            {moments.available ? 'No clear moment was found.' : 'This video has not been analyzed for moments yet.'}
            {moments.signals?.audio === 'unavailable' ? ' The video has no usable audio, so only camera movement was measured.' : ''}
          </p>
        )}
        {(moments.possible || []).length ? (
          <p className="mt-4 text-[0.9rem] text-body">
            <span className="font-semibold">Worth a glance: </span>
            {moments.possible.map((m) => `${formatSeconds(m.t_seconds)} (${m.score})`).join(', ')}
          </p>
        ) : null}
        {current.find((a) => a.kind === 'contact_sheet') ? (
          <div className="mt-4">
            <span className={EYEBROW}>Whole clip</span>
            <div className="mt-2"><Image artifact={current.find((a) => a.kind === 'contact_sheet')} alt="Contact sheet" /></div>
          </div>
        ) : null}
      </section>

      <section className={SECTION}>
        <h3 className={SECTION_TITLE}>Photos of the other vehicle</h3>
        <p className={SECTION_NOTE}>
          A dashcam rarely resolves a plate; a phone photo usually does. Time and location are read as soon as you add
          one. Text on the vehicle -- plate, fleet name, USDOT number -- is read at the next analysis.
        </p>
        <div className={ACTION_ROW}>
          <label className={`${GHOST_BTN} inline-block`}>
            {busy === 'upload' ? 'Uploading…' : 'Add photos'}
            <input
              ref={fileInput}
              type="file"
              accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif"
              multiple
              className="sr-only"
              disabled={busy === 'upload'}
              onChange={(event) => {
                const files = [...(event.target.files || [])]
                if (files.length) uploadPhotos(files)
              }}
            />
          </label>
          {photos.length ? (
            <button type="button" className={GHOST_BTN} onClick={readPhotos} disabled={busy === 'read'}>
              {busy === 'read' ? 'Requesting…' : 'Read text on photos'}
            </button>
          ) : null}
        </div>
        {photos.map(renderPhoto)}
      </section>

      <section className={SECTION}>
        <h3 className={SECTION_TITLE}>Other party</h3>
        <p className={SECTION_NOTE}>
          What you exchanged at the scene or got from the police report or an insurer. Encrypted, and never shown
          outside this page.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {OTHER_PARTY_FIELDS.map(([key, label]) => (
            <label key={key} className={FIELD}>
              {label}
              <input
                className={INPUT}
                value={otherParty[key] || ''}
                onChange={(event) => setOtherParty((current) => ({ ...current, [key]: event.target.value }))}
              />
            </label>
          ))}
        </div>
        <label className={`${FIELD} mt-3`}>
          Your notes
          <textarea className={INPUT} rows="4" value={notes} onChange={(event) => setNotes(event.target.value)} />
        </label>
        <div className={FORM_ACTIONS}>
          <button type="button" className={PRIMARY_BTN} onClick={save} disabled={busy === 'save'}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </button>
        </div>
      </section>

      <section className={SECTION}>
        <h3 className={SECTION_TITLE}>Look it up</h3>
        <p className={SECTION_NOTE}>
          These open in your browser; nothing is sent from this site. They identify vehicles and carriers, not people:
          to reach the driver of a rental or fleet vehicle, ask your insurer or the police to request the rental record.
        </p>
        {carrierLinks.length ? (
          <ul className="mt-3 grid gap-1 text-[0.92rem]">
            {carrierLinks.map((found) => (
              <li key={found.value}>
                <a className={LINK} href={found.lookup} target="_blank" rel="noreferrer">
                  FMCSA record for {found.kind.toUpperCase()} {found.value}
                </a>
              </li>
            ))}
          </ul>
        ) : null}
        {firstPlace ? (
          <p className="mt-3 text-[0.92rem]">
            <span className="font-semibold text-muted">Where it happened: </span>
            {mapLinks(firstPlace.lat, firstPlace.lon).map(([label, url]) => (
              <a key={label} className={`${LINK} mr-3`} href={url} target="_blank" rel="noreferrer">{label}</a>
            ))}
          </p>
        ) : null}
        <div className="mt-3 grid gap-4 sm:grid-cols-3">
          {LOOKUPS.map((group) => (
            <div key={group.group}>
              <span className={EYEBROW}>{group.group}</span>
              <ul className="mt-1 grid gap-1 text-[0.92rem]">
                {group.links.map(([label, url]) => (
                  <li key={url}><a className={LINK} href={url} target="_blank" rel="noreferrer">{label}</a></li>
                ))}
              </ul>
              {group.note ? <p className="mt-1 text-[0.8rem] text-muted">{group.note}</p> : null}
            </div>
          ))}
        </div>
      </section>

      <section className={SECTION}>
        <button type="button" className="cursor-pointer font-semibold text-ink" onClick={() => setShowLog((v) => !v)}>
          {showLog ? '▾' : '▸'} Who has opened this report
        </button>
        {showLog ? (
          <ul className="mt-2 grid gap-1 text-[0.88rem] text-body">
            {(report.access_log || []).map((entry, i) => (
              <li key={`${entry.at}-${i}`}>
                {formatClock(entry.at?.slice(0, 19))} · {entry.action} · {entry.clerk_user_id}
                {entry.as_admin ? ' (admin)' : ''}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </section>
  )
}

export default IncidentReport
