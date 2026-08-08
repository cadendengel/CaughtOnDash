import React, { useCallback, useEffect, useRef, useState } from 'react'
import { SignIn, UserButton, useAuth, useUser } from '@clerk/react'
import './App.css'
import './VideoDetail.css'

// Re-analysis is only offered on your own videos, and only from a state the
// backend will accept, so the button mirrors its rules rather than inviting a
// request that will 409.
const REQUEUEABLE_ANALYSIS_STATUSES = ['complete', 'failed', 'cancelled']

// Keep in step with STALE_PROCESSING_MINUTES in worker_services.py. A job whose
// worker has gone quiet for longer than this is wedged, not running, and its
// owner needs a way to re-queue it.
const STALE_PROCESSING_MS = 5 * 60 * 1000

const isStaleProcessing = (post) => {
  if (post?.analysis_status !== 'processing') {
    return false
  }

  if (!post.worker_last_seen_at) {
    return true
  }

  const lastSeen = new Date(post.worker_last_seen_at).getTime()
  if (Number.isNaN(lastSeen)) {
    return true
  }

  return Date.now() - lastSeen > STALE_PROCESSING_MS
}

function App() {
  const { isLoaded, isSignedIn, user } = useUser()
  const { getToken } = useAuth()
  const [activePage, setActivePage] = useState('feed')
  const [isAdmin, setIsAdmin] = useState(false)
  const [posts, setPosts] = useState([])
  const [tagsExpandedByPostId, setTagsExpandedByPostId] = useState({})
  const [uploadTags, setUploadTags] = useState([])
  const [uploadTagDraft, setUploadTagDraft] = useState('')
  const [adminTagEditsByPostId, setAdminTagEditsByPostId] = useState({})
  const [adminTagDraftsByPostId, setAdminTagDraftsByPostId] = useState({})
  const [adminTagSavingByPostId, setAdminTagSavingByPostId] = useState({})
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [searchHasSearched, setSearchHasSearched] = useState(false)
  const [commentsByPostId, setCommentsByPostId] = useState({})
  const [commentsVisibleByPostId, setCommentsVisibleByPostId] = useState({})
  const [commentDraftsByPostId, setCommentDraftsByPostId] = useState({})
  const [replyDraftsByCommentId, setReplyDraftsByCommentId] = useState({})
  const [replyComposerOpenByCommentId, setReplyComposerOpenByCommentId] = useState({})
  const [loadingCommentsByPostId, setLoadingCommentsByPostId] = useState({})
  const [likeLoadingByPostId, setLikeLoadingByPostId] = useState({})
  const [commentLoadingByPostId, setCommentLoadingByPostId] = useState({})
  const [commentLikeLoadingByCommentId, setCommentLikeLoadingByCommentId] = useState({})
  const [replyLoadingByCommentId, setReplyLoadingByCommentId] = useState({})
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [uploadSuccess, setUploadSuccess] = useState('')
  const [uploading, setUploading] = useState(false)
  const viewedVideoIdsRef = useRef(new Set())
  const [currentVideo, setCurrentVideo] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailReturnPage, setDetailReturnPage] = useState('feed')
  const [shareStatusByPostId, setShareStatusByPostId] = useState({})
  const [analysisRequestLoadingByPostId, setAnalysisRequestLoadingByPostId] = useState({})
  const [analysisRequestErrorByPostId, setAnalysisRequestErrorByPostId] = useState({})

  const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

  // Every backend call goes through this so the Clerk session token is attached
  // consistently. The backend treats the token as the authoritative identity;
  // the X-Clerk-User-Id header some callers still send is ignored once
  // REQUIRE_CLERK_JWT is enabled server-side.
  const authFetch = useCallback(
    async (url, options = {}) => {
      let token
      try {
        token = await getToken()
      } catch {
        // Signed out, or the token could not be refreshed. Fall through as an
        // anonymous request rather than breaking public reads like the feed.
        token = null
      }

      const headers = { ...(options.headers || {}) }
      if (token) {
        headers.Authorization = `Bearer ${token}`
      }

      return fetch(url, { ...options, headers })
    },
    [getToken],
  )

  const formatTimestamp = (value) => {
    if (!value) {
      return ''
    }

    const date = new Date(value)
    if (Number.isNaN(date.getTime())) {
      return value
    }

    const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000)
    const absoluteSeconds = Math.abs(diffSeconds)

    if (absoluteSeconds < 60) {
      return diffSeconds < 0 ? 'just now' : 'in a moment'
    }

    const units = [
      { label: 'year', seconds: 60 * 60 * 24 * 365 },
      { label: 'month', seconds: 60 * 60 * 24 * 30 },
      { label: 'day', seconds: 60 * 60 * 24 },
      { label: 'hour', seconds: 60 * 60 },
      { label: 'minute', seconds: 60 },
    ]

    const unit = units.find((entry) => absoluteSeconds >= entry.seconds) || units[units.length - 1]
    const valueInUnit = Math.round(absoluteSeconds / unit.seconds)
    const suffix = valueInUnit === 1 ? '' : 's'
    return diffSeconds < 0 ? `${valueInUnit} ${unit.label}${suffix} ago` : `in ${valueInUnit} ${unit.label}${suffix}`
  }

  const normalizeTagText = (value) => {
    return String(value || '').trim()
  }

  const normalizeTagSource = (value, fallback = 'user') => {
    const source = String(value || fallback).trim().toLowerCase()
    return ['user', 'admin', 'ai'].includes(source) ? source : fallback
  }

  const getDisplayName = (item) => {
    return item?.display_name || item?.username || 'Dash User'
  }

  const getHandle = (item) => {
    return item?.username || 'dash_user'
  }

  const normalizeTagObjects = (tags, fallbackSource = 'user') => {
    const seen = new Set()
    const normalized = []

    if (!Array.isArray(tags)) {
      return normalized
    }

    tags.forEach((tag) => {
      const rawTag = typeof tag === 'string' ? { text: tag, source: fallbackSource } : (tag || {})
      const text = normalizeTagText(rawTag.text || rawTag.name || rawTag.label)
      if (!text) {
        return
      }

      const source = normalizeTagSource(rawTag.source, fallbackSource)
      const dedupeKey = text.toLowerCase()
      if (seen.has(dedupeKey)) {
        return
      }

      seen.add(dedupeKey)
      normalized.push({ text, source })
    })

    return normalized
  }

  const addTagToList = (tags, value, source = 'user') => {
    return normalizeTagObjects([...(tags || []), { text: value, source }], source)
  }

  const removeTagAtIndex = (tags, index) => {
    return (tags || []).filter((_, tagIndex) => tagIndex !== index)
  }

  const getTagColorClass = (source) => {
    if (source === 'admin') {
      return 'tag-pill--admin'
    }

    if (source === 'ai') {
      return 'tag-pill--ai'
    }

    return 'tag-pill--user'
  }

  const getAnalysisStatusColor = (status) => {
    switch (status) {
      case 'complete':
        return '#10b981' // green
      case 'processing':
        return '#f59e0b' // amber
      case 'failed':
        return '#ef4444' // red
      case 'cancelled':
        return '#6b7280' // gray
      case 'pending':
      default:
        return '#9ca3af' // gray
    }
  }

  const renderAnalysisStatus = (post) => {
    // Approval comes first in the pipeline, so it comes first in the label.
    // Showing "Cancelled" or "Pending 0%" for a video nobody has looked at yet
    // describes the machinery rather than the situation.
    if (post.approval_status === 'pending_review') {
      return (
        <div className="analysis-status-container" style={{ marginTop: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ display: 'inline-block', width: '8px', height: '8px',
                           borderRadius: '50%', backgroundColor: '#9ca3af' }} />
            <span style={{ fontSize: '0.9rem', color: '#666' }}>Pending review</span>
          </div>
        </div>
      )
    }

    if (post.approval_status === 'rejected') {
      return (
        <div className="analysis-status-container" style={{ marginTop: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ display: 'inline-block', width: '8px', height: '8px',
                           borderRadius: '50%', backgroundColor: '#6b7280' }} />
            <span style={{ fontSize: '0.9rem', color: '#666' }}>Not selected for analysis</span>
          </div>
        </div>
      )
    }

    if (!post.analysis_status) {
      return null
    }

    const status = post.analysis_status
    const stage = post.analysis_stage || 'queued'
    const progress = post.analysis_progress || 0
    const color = getAnalysisStatusColor(status)

    let statusLabel = status.charAt(0).toUpperCase() + status.slice(1)
    if (status === 'processing') {
      statusLabel = `Processing: ${progress}%`
    }

    return (
      <div className="analysis-status-container" style={{ marginTop: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              display: 'inline-block',
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: color,
            }}
          />
          <span style={{ fontSize: '0.9rem', color: '#666' }}>{statusLabel}</span>
          {status === 'processing' && stage && (
            <span style={{ fontSize: '0.85rem', color: '#999' }}>({stage})</span>
          )}
        </div>
        {status === 'processing' && (
          <div style={{ marginTop: '6px', width: '100%', height: '4px', backgroundColor: '#e5e7eb', borderRadius: '2px', overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                width: `${progress}%`,
                backgroundColor: color,
                transition: 'width 0.3s ease',
              }}
            />
          </div>
        )}
      </div>
    )
  }


  const clerkEmail = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || ''
  const clerkFullName = [user?.firstName, user?.lastName].filter(Boolean).join(' ').trim() || user?.fullName || ''
  const clerkUsername =
    clerkFullName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '') ||
    clerkEmail.split('@')[0] ||
    ''
  const clerkDisplayName =
    clerkFullName ||
    clerkUsername ||
    'Dash User'

  const identityPayload = {
    clerk_user_id: user?.id,
    email: clerkEmail,
    username: clerkUsername,
    display_name: clerkDisplayName,
    avatar_url: user?.imageUrl || '',
  }

  // Longest edge of the generated poster frame. Big enough for a feed card and
  // a row in the worker's table, small enough not to slow the upload down.
  const POSTER_MAX_EDGE = 640

  /// Read duration and grab a poster frame in one decode.
  //
  // The browser is the only place a frame can be captured before analysis:
  // approval happens first, so the analyzer cannot supply the thumbnail that
  // the approval decision needs. Seeking a little way in avoids the black or
  // blank frame most videos open on.
  const readVideoPreview = (file) => {
    return new Promise((resolve) => {
      const videoElement = document.createElement('video')
      const objectUrl = URL.createObjectURL(file)
      let settled = false

      const finish = (duration, posterBlob) => {
        if (settled) return
        settled = true
        URL.revokeObjectURL(objectUrl)
        resolve({ durationSeconds: duration, poster: posterBlob })
      }

      const capture = (duration) => {
        try {
          const width = videoElement.videoWidth
          const height = videoElement.videoHeight
          if (!width || !height) {
            finish(duration, null)
            return
          }

          const scale = Math.min(1, POSTER_MAX_EDGE / Math.max(width, height))
          const canvas = document.createElement('canvas')
          canvas.width = Math.round(width * scale)
          canvas.height = Math.round(height * scale)
          canvas.getContext('2d').drawImage(videoElement, 0, 0, canvas.width, canvas.height)

          // A failed capture must not fail the upload; the poster is a nicety.
          canvas.toBlob((blob) => finish(duration, blob), 'image/jpeg', 0.8)
        } catch {
          finish(duration, null)
        }
      }

      videoElement.preload = 'auto'
      videoElement.muted = true
      videoElement.playsInline = true

      videoElement.onloadedmetadata = () => {
        const duration = Number.isFinite(videoElement.duration)
          ? Math.max(0, Math.round(videoElement.duration))
          : 0

        // A second in, or the midpoint of a very short clip.
        videoElement.currentTime = Math.min(1, Math.max(0, videoElement.duration / 2 || 0))
        videoElement.onseeked = () => capture(duration)

        // Some containers never fire onseeked; do not hang the upload on it.
        window.setTimeout(() => finish(duration, null), 4000)
      }

      videoElement.onerror = () => finish(0, null)
      videoElement.src = objectUrl
    })
  }

  const markVideoViewed = async (videoId) => {
    if (!videoId || viewedVideoIdsRef.current.has(videoId)) {
      return
    }

    viewedVideoIdsRef.current.add(videoId)

    try {
      await authFetch(`${API_BASE}/api/videos/${videoId}/view/`, {
        method: 'POST',
      })
    } catch (err) {
      // ignore view-count failures for now
    }
  }

  const loadFeed = async () => {
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      const res = await authFetch(`${API_BASE}/api/feed/`, { headers })
      if (!res.ok) return
      const data = await res.json()
      const items = data.items || []
      setPosts(items)
      return items
    } catch (err) {
      // ignore for now
    }
    return []
  }

  const loadSearchResults = async (query) => {
    const normalizedQuery = String(query || '').trim()

    setSearchError('')
    setSearchHasSearched(true)

    if (!normalizedQuery) {
      setSearchResults([])
      return []
    }

    setSearchLoading(true)
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      const response = await authFetch(
        `${API_BASE}/api/videos/search/?q=${encodeURIComponent(normalizedQuery)}&limit=20`,
        { headers },
      )

      if (!response.ok) {
        throw new Error('Could not search videos.')
      }

      const data = await response.json()
      const payload = data.payload || {}
      const items = payload.items || data.items || []
      setSearchResults(items)
      return items
    } catch (err) {
      setSearchResults([])
      setSearchError(err.message || 'Search failed.')
      return []
    } finally {
      setSearchLoading(false)
    }
  }

  const loadComments = async (videoId) => {
    if (!videoId) {
      return
    }

    setLoadingCommentsByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      const res = await authFetch(`${API_BASE}/api/videos/${videoId}/comments/`, { headers })
      if (!res.ok) return
      const data = await res.json()
      const items = data.items || []
      setCommentsByPostId((current) => ({ ...current, [videoId]: items }))
      return items
    } catch (err) {
      // ignore for now
    } finally {
      setLoadingCommentsByPostId((current) => ({ ...current, [videoId]: false }))
    }
    return []
  }

  const loadAdminOverview = async () => {
    const items = await loadFeed()
    setAdminTagEditsByPostId(
      items.reduce((accumulator, post) => {
        accumulator[post.id] = normalizeTagObjects(post.tags || [])
        return accumulator
      }, {}),
    )
    setAdminTagDraftsByPostId(
      items.reduce((accumulator, post) => {
        accumulator[post.id] = ''
        return accumulator
      }, {}),
    )
    await Promise.all(items.map((post) => loadComments(post.id)))
  }

  const loadCurrentUserSummary = async () => {
    if (!user?.id) {
      setIsAdmin(false)
      return
    }

    try {
      const res = await authFetch(`${API_BASE}/api/auth/me/`, {
        headers: {
          'X-Clerk-User-Id': user.id,
        },
      })

      if (!res.ok) {
        setIsAdmin(false)
        return
      }

      const data = await res.json()
      setIsAdmin(Boolean(data.is_admin))
    } catch (err) {
      setIsAdmin(false)
    }
  }

  const getDetailUrl = (videoId) => {
    if (typeof window === 'undefined') {
      return ''
    }

    const url = new URL(window.location.href)
    url.searchParams.set('video', videoId)
    return `${url.pathname}?${url.searchParams.toString()}`
  }

  const loadVideoDetail = async (videoId, options = {}) => {
    const { skipViewCount = false } = options
    if (!videoId) return
    if (!skipViewCount) {
      setDetailLoading(true)
      setCurrentVideo(null)
    }
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      if (skipViewCount) {
        headers['X-Skip-View-Count'] = '1'
      }
      const res = await authFetch(`${API_BASE}/api/videos/${videoId}/`, { headers })
      if (!res.ok) return
      const data = await res.json()
      // Response envelope: payload.video
      const video = (data.payload && data.payload.video) || data.video || null
      setCurrentVideo(video)
      // preload comments for the detail page
      await loadComments(videoId)
    } catch (err) {
      // ignore
    } finally {
      if (!skipViewCount) {
        setDetailLoading(false)
      }
    }
  }

  const openDetail = async (videoId, options = {}) => {
    const { updateHistory = true } = options
    const returnPage = activePage === 'detail' ? detailReturnPage : activePage

    setDetailReturnPage(returnPage || 'feed')
    setActivePage('detail')
    setCommentsVisibleByPostId((current) => ({ ...current, [videoId]: true }))
    if (updateHistory && typeof window !== 'undefined') {
      window.history.pushState({}, '', getDetailUrl(videoId))
    }
    await loadVideoDetail(videoId)
  }

  const openAdminPanel = async () => {
    if (!isAdmin) {
      return
    }

    setActivePage('admin')
    await loadAdminOverview()
  }

  const closeDetail = () => {
    setActivePage(detailReturnPage || 'feed')
    setCurrentVideo(null)
    if (typeof window !== 'undefined') {
      window.history.pushState({}, '', window.location.pathname)
    }
  }

  const toggleTagExpansion = (postId) => {
    if (!postId) {
      return
    }

    setTagsExpandedByPostId((current) => ({
      ...current,
      [postId]: !current[postId],
    }))
  }

  const updateUploadTagDraft = (value) => {
    setUploadTagDraft(value)
  }

  const addUploadTag = () => {
    const nextTag = normalizeTagText(uploadTagDraft)
    if (!nextTag) {
      return
    }

    setUploadTags((current) => addTagToList(current, nextTag, 'user'))
    setUploadTagDraft('')
  }

  const removeUploadTag = (index) => {
    setUploadTags((current) => removeTagAtIndex(current, index))
  }

  const updateAdminTagDraft = (videoId, value) => {
    setAdminTagDraftsByPostId((current) => ({
      ...current,
      [videoId]: value,
    }))
  }

  const addAdminTag = (videoId) => {
    const draft = normalizeTagText(adminTagDraftsByPostId[videoId])
    if (!draft) {
      return
    }

    setAdminTagEditsByPostId((current) => ({
      ...current,
      [videoId]: addTagToList(current[videoId] || [], draft, 'admin'),
    }))
    setAdminTagDraftsByPostId((current) => ({
      ...current,
      [videoId]: '',
    }))
  }

  const removeAdminTag = (videoId, index) => {
    setAdminTagEditsByPostId((current) => ({
      ...current,
      [videoId]: removeTagAtIndex(current[videoId] || [], index),
    }))
  }

  const saveAdminTags = async (videoId) => {
    if (!isAdmin || !videoId || adminTagSavingByPostId[videoId]) {
      return
    }

    const tags = normalizeTagObjects(adminTagEditsByPostId[videoId] || [])
    setAdminTagSavingByPostId((current) => ({ ...current, [videoId]: true }))

    try {
      const response = await authFetch(`${API_BASE}/api/videos/admin/videos/${videoId}/tags/`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-Clerk-User-Id': user?.id || '',
        },
        body: JSON.stringify({ tags }),
      })

      if (!response.ok) {
        throw new Error('Could not update tags.')
      }

      const data = await response.json()
      const updatedVideo = data.video || data.payload?.video || {}

      setPosts((current) =>
        current.map((post) => (post.id === videoId ? { ...post, ...updatedVideo, tags: updatedVideo.tags || tags } : post)),
      )
      setCurrentVideo((current) =>
        current && current.id === videoId ? { ...current, ...updatedVideo, tags: updatedVideo.tags || tags } : current,
      )
      setAdminTagEditsByPostId((current) => ({
        ...current,
        [videoId]: normalizeTagObjects(updatedVideo.tags || tags),
      }))
    } catch (err) {
      // ignore for now
    } finally {
      setAdminTagSavingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const shareVideo = async (post) => {
    if (!post?.id) {
      return
    }

    const shareUrl = getDetailUrl(post.id)
    const shareText = `${post.title} on CaughtOnDash`

    try {
      if (navigator.share) {
        await navigator.share({ title: post.title, text: shareText, url: shareUrl })
      } else {
        await navigator.clipboard.writeText(`${window.location.origin}${shareUrl}`)
      }

      setShareStatusByPostId((current) => ({ ...current, [post.id]: 'shared' }))
      window.setTimeout(() => {
        setShareStatusByPostId((current) => ({ ...current, [post.id]: '' }))
      }, 1400)
    } catch (err) {
      setShareStatusByPostId((current) => ({ ...current, [post.id]: 'failed' }))
      window.setTimeout(() => {
        setShareStatusByPostId((current) => ({ ...current, [post.id]: '' }))
      }, 1400)
    }
  }

  const renderTagPills = (videoId, tags, options = {}) => {
    const tagList = normalizeTagObjects(tags || [])
    const editable = Boolean(options.editable)
    if (tagList.length === 0 && !editable) {
      return null
    }

    const isExpanded = Boolean(tagsExpandedByPostId[videoId])
    const visibleTags = isExpanded || tagList.length <= 3 ? tagList : tagList.slice(0, 3)
    const hiddenCount = tagList.length - visibleTags.length
    const showToggle = editable || tagList.length > 3

    return (
      <div className={editable ? 'tag-strip tag-strip--editable' : 'tag-strip'}>
        {visibleTags.length > 0 ? (
          visibleTags.map((tag, index) => (
            <span key={`${tag.text}-${index}`} className={`tag-pill ${getTagColorClass(tag.source)}`}>
              {tag.text}
            </span>
          ))
        ) : editable ? (
          <span className="tag-pill tag-pill--toggle">No tags yet</span>
        ) : null}

        {showToggle ? (
          <button
            type="button"
            className="tag-pill tag-pill--toggle"
            onClick={() => toggleTagExpansion(videoId)}
            aria-expanded={isExpanded}
          >
            {isExpanded ? 'Hide tags' : hiddenCount > 0 ? `+${hiddenCount} more` : editable ? 'Add tags' : 'Manage tags'}
          </button>
        ) : null}
      </div>
    )
  }

  const renderTagEditor = (videoId, tags) => {
    const currentTags = normalizeTagObjects(adminTagEditsByPostId[videoId] || tags || [])
    const draftValue = adminTagDraftsByPostId[videoId] || ''

    return (
      <div className="tag-editor">
        <div className="tag-editor-head">
          <span className="tag-editor-label">Edit tags</span>
          <button type="button" className="ghost-btn" onClick={() => toggleTagExpansion(videoId)}>
            Collapse
          </button>
        </div>

        <div className="tag-editor-chip-list">
          {currentTags.map((tag, index) => (
            <span key={`${tag.text}-${index}`} className={`tag-pill ${getTagColorClass(tag.source)}`}>
              {tag.text}
              <button type="button" className="tag-chip-remove" onClick={() => removeAdminTag(videoId, index)}>
                ×
              </button>
            </span>
          ))}
        </div>

        <div className="tag-editor-row">
          <input
            type="text"
            className="tag-input"
            value={draftValue}
            onChange={(event) => updateAdminTagDraft(videoId, event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                addAdminTag(videoId)
              }
            }}
            placeholder="Add an admin tag"
          />
          <button type="button" className="secondary-btn" onClick={() => addAdminTag(videoId)}>
            Add
          </button>
        </div>

        <div className="form-actions tag-editor-actions">
          <button type="button" className="primary-btn" onClick={() => saveAdminTags(videoId)} disabled={adminTagSavingByPostId[videoId]}>
            {adminTagSavingByPostId[videoId] ? 'Saving...' : 'Save tags'}
          </button>
        </div>
      </div>
    )
  }

  const toggleComments = async (videoId) => {
    if (!videoId) {
      return
    }

    const nextVisible = !commentsVisibleByPostId[videoId]
    setCommentsVisibleByPostId((current) => ({ ...current, [videoId]: nextVisible }))

    if (nextVisible && !commentsByPostId[videoId]) {
      await loadComments(videoId)
    }
  }

  const updateCommentTree = (comments, commentId, updater) => {
    return (comments || []).map((comment) => {
      if (comment.id === commentId) {
        return updater(comment)
      }

      if (Array.isArray(comment.replies) && comment.replies.length > 0) {
        return {
          ...comment,
          replies: updateCommentTree(comment.replies, commentId, updater),
        }
      }

      return comment
    })
  }

  const openReplyComposer = (commentId, username) => {
    const replyHandle = username ? `@${username}` : ''
    setReplyComposerOpenByCommentId((current) => ({ ...current, [commentId]: true }))
    setReplyDraftsByCommentId((current) => {
      const existingDraft = current[commentId] || ''
      if (existingDraft.trim().length > 0) {
        return current
      }

      return {
        ...current,
        [commentId]: replyHandle ? `${replyHandle} ` : '',
      }
    })
  }

  const closeReplyComposer = (commentId) => {
    setReplyComposerOpenByCommentId((current) => ({ ...current, [commentId]: false }))
  }

  const toggleCommentLike = async (videoId, commentId) => {
    if (!videoId || !commentId || commentLikeLoadingByCommentId[commentId]) {
      return
    }

    setCommentLikeLoadingByCommentId((current) => ({ ...current, [commentId]: true }))
    try {
      const response = await authFetch(`${API_BASE}/api/videos/comments/${commentId}/like/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(identityPayload),
      })

      if (!response.ok) {
        throw new Error('Could not update comment like.')
      }

      const data = await response.json()
      const result = data.comment || {}

      setCommentsByPostId((current) => ({
        ...current,
        [videoId]: updateCommentTree(current[videoId] || [], commentId, (comment) => ({
          ...comment,
          likes_count: result.likes_count ?? comment.likes_count,
          liked: result.liked ?? comment.liked,
        })),
      }))
    } catch (err) {
      // ignore for now
    } finally {
      setCommentLikeLoadingByCommentId((current) => ({ ...current, [commentId]: false }))
    }
  }

  const handleReplySubmit = async (videoId, parentComment, event) => {
    event.preventDefault()
    if (!videoId || !parentComment?.id || replyLoadingByCommentId[parentComment.id]) {
      return
    }

    const text = (replyDraftsByCommentId[parentComment.id] || '').trim()
    if (!text) {
      return
    }

    setReplyLoadingByCommentId((current) => ({ ...current, [parentComment.id]: true }))
    try {
      const response = await authFetch(`${API_BASE}/api/videos/${videoId}/comments/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...identityPayload,
          text,
          parent_comment_id: parentComment.id,
        }),
      })

      if (!response.ok) {
        throw new Error('Could not post reply.')
      }

      setReplyDraftsByCommentId((current) => ({ ...current, [parentComment.id]: '' }))
      closeReplyComposer(parentComment.id)
      await loadVideoDetail(videoId, { skipViewCount: true })
      await loadFeed()
    } catch (err) {
      // ignore for now
    } finally {
      setReplyLoadingByCommentId((current) => ({ ...current, [parentComment.id]: false }))
    }
  }

  const toggleLike = async (videoId) => {
    if (!videoId || likeLoadingByPostId[videoId]) {
      return
    }

    setLikeLoadingByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const response = await authFetch(`${API_BASE}/api/videos/${videoId}/like/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(identityPayload),
      })

      if (!response.ok) {
        throw new Error('Could not update like.')
      }

      const data = await response.json()
      const result = data.video || (data.payload && data.payload.video) || {}
      const applyLikeState = (item) =>
        item && item.id === videoId
          ? {
              ...item,
              likes_count: result.likes_count ?? item.likes_count,
              liked: result.liked ?? item.liked,
            }
          : item

      setPosts((current) =>
        current.map(applyLikeState),
      )
      setSearchResults((current) => current.map(applyLikeState))
      setCurrentVideo((current) =>
        applyLikeState(current),
      )
      setSearchResults((current) =>
        current.map((post) =>
          post.id === videoId
            ? {
                ...post,
                likes_count: result.likes_count ?? post.likes_count,
                liked: result.liked ?? post.liked,
              }
            : post,
        ),
      )
    } catch (err) {
      // ignore for now
    } finally {
      setLikeLoadingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const canRequestAnalysis = (post) => {
    if (!post || !user?.id) {
      return false
    }

    if (post.status !== 'ready') {
      return false
    }

    if (!(post.owner_clerk_user_id === user.id || isAdmin)) {
      return false
    }

    return REQUEUEABLE_ANALYSIS_STATUSES.includes(post.analysis_status) || isStaleProcessing(post)
  }

  // Reviewing is offered to the owner and to admins, matching the backend.
  const canReview = (post) => {
    if (!post || !user?.id) {
      return false
    }

    if (post.approval_status !== 'pending_review') {
      return false
    }

    return post.owner_clerk_user_id === user.id || isAdmin
  }

  const decideApproval = async (videoId, approve) => {
    if (!videoId || analysisRequestLoadingByPostId[videoId]) {
      return
    }

    setAnalysisRequestLoadingByPostId((current) => ({ ...current, [videoId]: true }))
    setAnalysisRequestErrorByPostId((current) => ({ ...current, [videoId]: '' }))

    try {
      const response = await authFetch(`${API_BASE}/api/videos/${videoId}/approval/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approve }),
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        setAnalysisRequestErrorByPostId((current) => ({
          ...current,
          [videoId]: data.detail || 'Could not record that decision.',
        }))
        return
      }

      const updated = data.video || (data.payload && data.payload.video) || {}
      const applyDecision = (item) =>
        item && item.id === videoId
          ? {
              ...item,
              approval_status: updated.approval_status ?? (approve ? 'approved' : 'rejected'),
              analysis_status: updated.analysis_status ?? item.analysis_status,
              analysis_stage: updated.analysis_stage ?? item.analysis_stage,
              analysis_progress: updated.analysis_progress ?? 0,
            }
          : item

      setPosts((current) => current.map(applyDecision))
      setSearchResults((current) => current.map(applyDecision))
      setCurrentVideo((current) => applyDecision(current))
    } catch {
      setAnalysisRequestErrorByPostId((current) => ({
        ...current,
        [videoId]: 'Could not reach the server. Please try again.',
      }))
    } finally {
      setAnalysisRequestLoadingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const requestAnalysis = async (videoId) => {
    if (!videoId || analysisRequestLoadingByPostId[videoId]) {
      return
    }

    setAnalysisRequestLoadingByPostId((current) => ({ ...current, [videoId]: true }))
    setAnalysisRequestErrorByPostId((current) => ({ ...current, [videoId]: '' }))

    try {
      const response = await authFetch(`${API_BASE}/api/videos/${videoId}/analyze/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        // Surface the backend's reason ("Analysis is already queued", "Video is
        // not ready for analysis yet") rather than a generic failure.
        setAnalysisRequestErrorByPostId((current) => ({
          ...current,
          [videoId]: data.detail || 'Could not request analysis.',
        }))
        return
      }

      const updated = data.video || (data.payload && data.payload.video) || {}
      const applyAnalysisState = (item) =>
        item && item.id === videoId
          ? {
              ...item,
              analysis_status: updated.analysis_status ?? 'pending',
              analysis_stage: updated.analysis_stage ?? 'queued',
              analysis_progress: updated.analysis_progress ?? 0,
              analysis_error: updated.analysis_error ?? '',
            }
          : item

      setPosts((current) => current.map(applyAnalysisState))
      setSearchResults((current) => current.map(applyAnalysisState))
      setCurrentVideo((current) => applyAnalysisState(current))
    } catch {
      // Network-level failure. Binding the error and reading err.message here
      // makes the React Compiler bail on this whole component, which silences
      // its diagnostics for the entire file -- so keep this message static.
      setAnalysisRequestErrorByPostId((current) => ({
        ...current,
        [videoId]: 'Could not reach the server. Please try again.',
      }))
    } finally {
      setAnalysisRequestLoadingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const deleteAdminVideo = async (videoId) => {
    if (!isAdmin || !videoId) {
      return
    }

    try {
      const response = await authFetch(`${API_BASE}/api/videos/admin/videos/${videoId}/`, {
        method: 'DELETE',
        headers: {
          'X-Clerk-User-Id': user?.id || '',
        },
      })

      if (!response.ok) {
        throw new Error('Could not delete video.')
      }

      setPosts((current) => current.filter((post) => post.id !== videoId))
      setCommentsByPostId((current) => {
        const next = { ...current }
        delete next[videoId]
        return next
      })
      if (currentVideo?.id === videoId) {
        closeDetail()
      }
      await loadFeed()
    } catch (err) {
      // ignore for now
    }
  }

  const deleteAdminComment = async (videoId, commentId) => {
    if (!isAdmin || !videoId || !commentId) {
      return
    }

    try {
      const response = await authFetch(`${API_BASE}/api/videos/admin/comments/${commentId}/`, {
        method: 'DELETE',
        headers: {
          'X-Clerk-User-Id': user?.id || '',
        },
      })

      if (!response.ok) {
        throw new Error('Could not delete comment.')
      }

      await loadComments(videoId)
      await loadFeed()
      if (currentVideo?.id === videoId) {
        await loadVideoDetail(videoId, { skipViewCount: true })
      }
    } catch (err) {
      // ignore for now
    }
  }

  const handleCommentSubmit = async (videoId, event) => {
    event.preventDefault()
    if (!videoId || commentLoadingByPostId[videoId]) {
      return
    }

    const text = (commentDraftsByPostId[videoId] || '').trim()
    if (!text) {
      return
    }

    setCommentLoadingByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const response = await authFetch(`${API_BASE}/api/videos/${videoId}/comments/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...identityPayload,
          text,
        }),
      })

      if (!response.ok) {
        throw new Error('Could not post comment.')
      }

      setCommentDraftsByPostId((current) => ({ ...current, [videoId]: '' }))
      await loadVideoDetail(videoId, { skipViewCount: true })
      await loadFeed()
    } catch (err) {
      // ignore for now
    } finally {
      setCommentLoadingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const syncProfile = async () => {
    if (!user?.id) {
      return
    }

    try {
      await authFetch(`${API_BASE}/api/auth/bootstrap/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(identityPayload),
      })
    } catch (err) {
      // ignore bootstrap failures for now
    }
  }

  useEffect(() => {
    if (!isSignedIn || !user?.id) {
      return
    }

    const syncAndLoad = async () => {
      await syncProfile()
      await loadCurrentUserSummary()
      await loadFeed()

      const videoIdFromUrl = typeof window === 'undefined'
        ? ''
        : new URL(window.location.href).searchParams.get('video') || ''

      if (videoIdFromUrl) {
        await openDetail(videoIdFromUrl, { updateHistory: false })
      }
    }

    syncAndLoad()
  }, [isSignedIn, user?.id])

  useEffect(() => {
    if (activePage === 'admin' && !isAdmin) {
      setActivePage('feed')
    }
  }, [activePage, isAdmin])

  if (!isLoaded) {
    return (
      <main className="screen loading-state">
        <p>Loading authentication…</p>
      </main>
    )
  }

  if (!isSignedIn) {
    return (
      <main className="screen auth-screen">
        <section className="auth-panel">
          <div className="auth-copy">
            <span className="eyebrow">CaughtOnDash</span>
            <h1>Sign in to continue</h1>
            <p>
              Use your Clerk account to get into the dashboard and continue
              where you left off.
            </p>
          </div>

          <div className="auth-widget" aria-label="Sign in form">
            <SignIn routing="virtual" />
          </div>
        </section>
      </main>
    )
  }

  const renderPostCard = (post) => (
    <article key={post.id} className="feed-card">
      <div className="feed-card-head">
        <div className="author-block">
          <span className="author-name">{getDisplayName(post)}</span>
          <span className="author-handle">@{getHandle(post)}</span>
        </div>
        <span className="timestamp">{formatTimestamp(post.created_at)}</span>
      </div>

      <h2>{post.title}</h2>

      {renderTagPills(post.id, post.tags || [])}
      {renderAnalysisStatus(post)}

      {post.playback_url ? (
        <video
          className="feed-video"
          controls
          playsInline
          preload="metadata"
          poster={post.thumbnail_url || undefined}
          onPlay={() => markVideoViewed(post.id)}
        >
          <source src={post.playback_url} type="video/mp4" />
          Your browser does not support the video tag.
        </video>
      ) : (
        <div className="feed-video-placeholder">
          Video not available yet.
        </div>
      )}

      <div className="post-actions feed-actions-row">
        <button
          type="button"
          className={post.liked ? 'ghost-btn active' : 'ghost-btn'}
          onClick={() => toggleLike(post.id)}
          disabled={likeLoadingByPostId[post.id]}
          aria-pressed={Boolean(post.liked)}
        >
          {post.liked ? 'Unlike' : 'Like'} · {post.likes_count || 0}
        </button>
        <button
          type="button"
          className="ghost-btn"
          onClick={() => openDetail(post.id)}
        >
          Comment · {post.comments_count || 0}
        </button>
        <button type="button" className="ghost-btn" onClick={() => shareVideo(post)}>
          {shareStatusByPostId[post.id] === 'shared' ? 'Copied' : 'Share'}
        </button>
        {canRequestAnalysis(post) ? (
          <button
            type="button"
            className="ghost-btn"
            onClick={() => requestAnalysis(post.id)}
            disabled={analysisRequestLoadingByPostId[post.id]}
          >
            {analysisRequestLoadingByPostId[post.id] ? 'Queueing...' : 'Re-analyze'}
          </button>
        ) : null}
        {canReview(post) ? (
          <>
            <button
              type="button"
              className="ghost-btn"
              onClick={() => decideApproval(post.id, true)}
              disabled={analysisRequestLoadingByPostId[post.id]}
            >
              {analysisRequestLoadingByPostId[post.id] ? 'Working...' : 'Approve for analysis'}
            </button>
            <button
              type="button"
              className="ghost-btn"
              onClick={() => decideApproval(post.id, false)}
              disabled={analysisRequestLoadingByPostId[post.id]}
            >
              Skip
            </button>
          </>
        ) : null}
      </div>

      {analysisRequestErrorByPostId[post.id] ? (
        <p className="analysis-request-error">{analysisRequestErrorByPostId[post.id]}</p>
      ) : null}
    </article>
  )

  const renderCommentNode = (comment, videoId, depth = 0, showAdminActions = false) => {
    const handle = getHandle(comment)
    const isReply = depth > 0

    return (
      <article key={comment.id} className={isReply ? 'comment-card comment-reply-card' : 'comment-card'}>
        <div className="comment-head">
          <div className="comment-author-block">
            <span className="comment-author-name">{getDisplayName(comment)}</span>
            <span className="comment-author-handle">@{handle}</span>
          </div>
          <span className="comment-timestamp">{formatTimestamp(comment.created_at)}</span>
        </div>

        <p>{comment.text}</p>

        <div className="comment-actions">
          <button
            type="button"
            className={comment.liked ? 'ghost-btn active' : 'ghost-btn'}
            onClick={() => toggleCommentLike(videoId, comment.id)}
            disabled={commentLikeLoadingByCommentId[comment.id]}
            aria-pressed={Boolean(comment.liked)}
          >
            {comment.liked ? 'Unlike' : 'Like'} · {comment.likes_count || 0}
          </button>

          {!isReply ? (
            <button
              type="button"
              className="ghost-btn"
              onClick={() => openReplyComposer(comment.id, handle)}
            >
              Reply
            </button>
          ) : null}

          {showAdminActions && isAdmin ? (
            <button type="button" className="danger-btn" onClick={() => deleteAdminComment(videoId, comment.id)}>
              Delete
            </button>
          ) : null}
        </div>

        {!isReply && replyComposerOpenByCommentId[comment.id] ? (
          <form className="comment-form reply-form" onSubmit={(event) => handleReplySubmit(videoId, comment, event)}>
            <textarea
              className="comment-input"
              value={replyDraftsByCommentId[comment.id] || ''}
              onChange={(event) =>
                setReplyDraftsByCommentId((current) => ({
                  ...current,
                  [comment.id]: event.target.value,
                }))
              }
              rows="2"
              placeholder={`Reply to @${handle}`}
            />
            <div className="form-actions reply-actions-row">
              <button type="submit" className="primary-btn" disabled={replyLoadingByCommentId[comment.id]}>
                {replyLoadingByCommentId[comment.id] ? 'Posting...' : 'Reply'}
              </button>
              <button type="button" className="secondary-btn" onClick={() => closeReplyComposer(comment.id)}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {!isReply && Array.isArray(comment.replies) && comment.replies.length > 0 ? (
          <div className="comment-replies">
            {comment.replies.map((reply) => renderCommentNode(reply, videoId, depth + 1, showAdminActions))}
          </div>
        ) : null}
      </article>
    )
  }

  const renderAdminPage = () => (
    <section className="page-content admin-page">
      <div className="page-heading">
        <h2>Admin</h2>
        <p>Admin-only moderation tools for posts, comments, and replies.</p>
      </div>

      {posts.length === 0 ? (
        <div className="empty-feed-card">
          <p className="eyebrow">Nothing to moderate</p>
          <h3>No posts available</h3>
          <p>When content appears, it will show here with delete controls.</p>
        </div>
      ) : (
        <div className="admin-list">
          {posts.map((post) => (
            <article key={post.id} className="feed-card admin-card">
              <div className="feed-card-head">
                <div className="author-block">
                  <span className="author-name">{getDisplayName(post)}</span>
                  <span className="author-handle">@{getHandle(post)}</span>
                </div>
                <span className="timestamp">{formatTimestamp(post.created_at)}</span>
              </div>

              <h2>{post.title}</h2>

              {renderTagPills(post.id, adminTagEditsByPostId[post.id] || post.tags || [], { editable: true })}

              {Boolean(tagsExpandedByPostId[post.id]) ? renderTagEditor(post.id, post.tags || []) : null}

              {renderAnalysisStatus(post)}

              <div className="video-meta">
                <span>{post.duration_seconds ? `${post.duration_seconds}s` : 'Duration unavailable'}</span>
                <span>{post.views || 0} views</span>
                <span>{post.likes_count || 0} likes</span>
                <span>{post.comments_count || 0} comments</span>
              </div>

              <div className="post-actions admin-actions-row">
                <button type="button" className="danger-btn" onClick={() => deleteAdminVideo(post.id)}>
                  Delete post
                </button>
              </div>

              <div className="comments-panel admin-comments-panel">
                {loadingCommentsByPostId[post.id] ? (
                  <p className="comments-empty">Loading comments...</p>
                ) : null}

                {!loadingCommentsByPostId[post.id] && (commentsByPostId[post.id] || []).length === 0 ? (
                  <p className="comments-empty">No comments yet.</p>
                ) : null}

                <div className="comments-list">
                  {(commentsByPostId[post.id] || []).map((comment) => renderCommentNode(comment, post.id, 0, true))}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )

  const handleSearchSubmit = async (event) => {
    event.preventDefault()
    await loadSearchResults(searchQuery)
  }

  const renderSearchPage = () => (
    <section className="page-content search-page">
      <div className="page-heading">
        <h2>Search</h2>
        <p>Find clips by title, description, or tags.</p>
      </div>

      <form className="search-panel" onSubmit={handleSearchSubmit}>
        <label className="search-input-group">
          <span>Search videos</span>
          <div className="search-input-row">
            <input
              type="text"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="freeway merge, near miss, brake check..."
            />
            <button type="submit" className="primary-btn" disabled={searchLoading}>
              {searchLoading ? 'Searching...' : 'Search'}
            </button>
          </div>
        </label>

        <div className="search-panel-actions">
          <button type="button" className="secondary-btn" onClick={() => loadSearchResults(searchQuery)} disabled={searchLoading}>
            Refresh results
          </button>
          <button
            type="button"
            className="ghost-btn"
            onClick={() => {
              setSearchQuery('')
              setSearchResults([])
              setSearchError('')
              setSearchHasSearched(false)
            }}
          >
            Clear
          </button>
        </div>
      </form>

      {searchError ? <p className="form-message error">{searchError}</p> : null}

      {searchHasSearched && !searchLoading && !searchError ? (
        <div className="search-summary">
          <span>{searchResults.length} result{searchResults.length === 1 ? '' : 's'}</span>
          {searchQuery.trim() ? <span>for “{searchQuery.trim()}”</span> : null}
        </div>
      ) : null}

      {!searchHasSearched && !searchLoading ? (
        <div className="empty-feed-card search-empty-card">
          <p className="eyebrow">Start here</p>
          <h3>Search the video catalog</h3>
          <p>
            Try a title, a common driving phrase, or a tag to find matching clips.
          </p>
        </div>
      ) : null}

      {searchLoading ? <p className="comments-empty">Searching…</p> : null}

      {searchHasSearched && !searchLoading && searchResults.length === 0 && !searchError ? (
        <div className="empty-feed-card search-empty-card">
          <p className="eyebrow">No matches</p>
          <h3>Nothing matched your search</h3>
          <p>Try fewer words or search by a tag name instead.</p>
        </div>
      ) : null}

      {searchResults.length > 0 ? <div className="feed-list">{searchResults.map(renderPostCard)}</div> : null}
    </section>
  )

  const handleUploadSubmit = async (event) => {
    event.preventDefault()
    setUploadError('')
    setUploadSuccess('')

    if (!uploadFile) {
      setUploadError('Choose a video file first.')
      return
    }

    if (!user?.id) {
      setUploadError('Missing Clerk user id.')
      return
    }

    setUploading(true)
    try {
      const { durationSeconds, poster } = await readVideoPreview(uploadFile)

      const bootstrapResponse = await authFetch(`${API_BASE}/api/videos/upload-url/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...identityPayload,
          title: uploadTitle || uploadFile.name,
          description: uploadDescription,
          original_filename: uploadFile.name,
          duration_seconds: durationSeconds,
          tags: uploadTags,
        }),
      })

      if (!bootstrapResponse.ok) {
        throw new Error('Could not create upload record.')
      }

      const bootstrapData = await bootstrapResponse.json()
      const video = bootstrapData.video

      const formData = new FormData()
      formData.append('video_id', video.id)
      formData.append('file', uploadFile)
      if (poster) {
        // Sent with the video rather than as a second request: one round trip,
        // and the thumbnail lands at the same moment the video becomes ready.
        formData.append('thumbnail', poster, 'poster.jpg')
      }

      const uploadResponse = await authFetch(`${API_BASE}/api/videos/upload/`, {
        method: 'POST',
        body: formData,
      })

      if (!uploadResponse.ok) {
        const failure = await uploadResponse.json().catch(() => ({}))
        throw new Error(failure.detail || 'Upload failed.')
      }

      setUploadSuccess('Video uploaded successfully.')
      setUploadTitle('')
      setUploadDescription('')
      setUploadFile(null)
      setUploadTags([])
      setUploadTagDraft('')
      await loadFeed()
      setActivePage('feed')
    } catch (err) {
      setUploadError(err.message || 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  const renderFeedPage = () => (
    <section className="page-content">
      <div className="page-heading">
        <h2>Feed</h2>
        <p>Latest dashcam uploads and incidents from the community.</p>
      </div>

      {posts.length === 0 ? (
        <div className="empty-feed-card">
          <p className="eyebrow">No posts yet</p>
          <h3>Your feed is empty</h3>
          <p>
            New dashcam videos will appear here once users start posting.
            Use the Post Video page to add the first upload.
          </p>
          <button
            type="button"
            className="primary-btn"
            onClick={() => setActivePage('post-video')}
          >
            Go to Post Video
          </button>
        </div>
      ) : (
        <div className="feed-list">{posts.map(renderPostCard)}</div>
      )}
    </section>
  )

  const renderDetailPage = () => {
    const video = currentVideo
    if (detailLoading) {
      return (
        <section className="page-content">
          <div className="page-heading">
            <h2>Loading…</h2>
          </div>
          <p>Loading video details…</p>
        </section>
      )
    }

    if (!video) {
      return (
        <section className="page-content">
          <div className="page-heading">
            <h2>Video not found</h2>
          </div>
          <p>The requested video could not be loaded.</p>
          <div className="form-actions">
            <button type="button" className="secondary-btn" onClick={() => setActivePage('feed')}>
              Back to Feed
            </button>
          </div>
        </section>
      )
    }

    return (
      <section className="page-content video-detail-page">
        <div className="page-heading">
          <div className="author-block detail-author-block">
            <span className="author-name">{getDisplayName(video)}</span>
            <span className="author-handle">@{getHandle(video)}</span>
          </div>
          <h2>{video.title}</h2>
        </div>

        {renderTagPills(video.id, video.tags || [])}

        {renderAnalysisStatus(video)}

        {video.playback_url ? (
          <video className="detail-video" controls preload="metadata">
            <source src={video.playback_url} type="video/mp4" />
            Your browser does not support the video tag.
          </video>
        ) : (
          <div className="feed-video-placeholder">Video not available yet.</div>
        )}

        <p className="detail-description">{video.description}</p>

        <div className="video-meta detail-meta-row">
          <span>{video.duration_seconds ? `${video.duration_seconds}s` : 'Duration unavailable'}</span>
          <span>{video.views} views</span>
          <span>{video.likes_count || 0} likes</span>
          <span>{video.comments_count || 0} comments</span>
          <span>{video.shares_count || 0} shares</span>
        </div>

        <div className="post-actions detail-actions-row">
          <button type="button" className="secondary-btn" onClick={closeDetail}>
            Back to Feed
          </button>
          {canRequestAnalysis(video) ? (
            <button
              type="button"
              className="ghost-btn"
              onClick={() => requestAnalysis(video.id)}
              disabled={analysisRequestLoadingByPostId[video.id]}
            >
              {analysisRequestLoadingByPostId[video.id] ? 'Queueing...' : 'Re-analyze'}
            </button>
          ) : null}
          {canReview(video) ? (
            <>
              <button
                type="button"
                className="ghost-btn"
                onClick={() => decideApproval(video.id, true)}
                disabled={analysisRequestLoadingByPostId[video.id]}
              >
                {analysisRequestLoadingByPostId[video.id] ? 'Working...' : 'Approve for analysis'}
              </button>
              <button
                type="button"
                className="ghost-btn"
                onClick={() => decideApproval(video.id, false)}
                disabled={analysisRequestLoadingByPostId[video.id]}
              >
                Skip
              </button>
            </>
          ) : null}
        </div>

        {analysisRequestErrorByPostId[video.id] ? (
          <p className="analysis-request-error">{analysisRequestErrorByPostId[video.id]}</p>
        ) : null}

        <div className="comments-panel detail-comments detail-comments-sheet">
          {loadingCommentsByPostId[video.id] ? (
            <p className="comments-empty">Loading comments...</p>
          ) : null}

          {!loadingCommentsByPostId[video.id] && (commentsByPostId[video.id] || []).length === 0 ? (
            <p className="comments-empty">No comments yet. Be the first to reply.</p>
          ) : null}

          <div className="comments-list">
            {(commentsByPostId[video.id] || []).map((comment) => renderCommentNode(comment, video.id))}
          </div>

          <form className="comment-form" onSubmit={(event) => handleCommentSubmit(video.id, event)}>
            <textarea
              className="comment-input"
              value={commentDraftsByPostId[video.id] || ''}
              onChange={(event) =>
                setCommentDraftsByPostId((current) => ({
                  ...current,
                  [video.id]: event.target.value,
                }))
              }
              rows="3"
              placeholder="Add a comment..."
            />
            <button type="submit" className="primary-btn" disabled={commentLoadingByPostId[video.id]}>
              {commentLoadingByPostId[video.id] ? 'Posting...' : 'Post comment'}
            </button>
          </form>
        </div>
      </section>
    )
  }

  const renderPostVideoPage = () => (
    <section className="page-content">
      <div className="page-heading">
        <h2>Post Video</h2>
        <p>Upload a dashcam clip and add details for the feed.</p>
      </div>

      <form className="post-video-card upload-form" onSubmit={handleUploadSubmit}>
        <label>
          <span>Title</span>
          <input
            type="text"
            value={uploadTitle}
            onChange={(event) => setUploadTitle(event.target.value)}
            placeholder="Late-night freeway clip"
          />
        </label>

        <label>
          <span>Description</span>
          <textarea
            value={uploadDescription}
            onChange={(event) => setUploadDescription(event.target.value)}
            placeholder="Tell people what happened..."
            rows="4"
          />
        </label>

          <div className="tag-input-section">
            <span className="tag-input-label">Tags</span>
            <div className="tag-editor-row">
              <input
                type="text"
                className="tag-input"
                value={uploadTagDraft}
                onChange={(event) => updateUploadTagDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    addUploadTag()
                  }
                }}
                placeholder="Add a tag and press Enter"
              />
              <button type="button" className="secondary-btn" onClick={addUploadTag}>
                Add
              </button>
            </div>

            {uploadTags.length > 0 ? (
              <div className="tag-editor-chip-list">
                {uploadTags.map((tag, index) => (
                  <span key={`${tag.text}-${index}`} className={`tag-pill ${getTagColorClass(tag.source)}`}>
                    {tag.text}
                    <button type="button" className="tag-chip-remove" onClick={() => removeUploadTag(index)}>
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
          </div>

        <label>
          <span>Video file</span>
          <input
            type="file"
            accept="video/*"
            onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
          />
        </label>

        {uploadError ? <p className="form-message error">{uploadError}</p> : null}
        {uploadSuccess ? <p className="form-message success">{uploadSuccess}</p> : null}

        <div className="form-actions">
          <button type="submit" className="primary-btn" disabled={uploading}>
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
          <button type="button" className="secondary-btn" onClick={() => setActivePage('feed')}>
            Back to Feed
          </button>
        </div>
      </form>
    </section>
  )

  return (
    <main className="screen app-shell">
      <header className="navbar">
        <div className="brand-block">
          <span className="eyebrow">CaughtOnDash</span>
          <h1>Community</h1>
        </div>

        <nav className="nav-links" aria-label="Main navigation">
          <button
            type="button"
            className={activePage === 'feed' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('feed')}
          >
            Feed
          </button>
          <button
            type="button"
            className={activePage === 'search' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('search')}
          >
            Search
          </button>
          <button
            type="button"
            className={activePage === 'post-video' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('post-video')}
          >
            Post Video
          </button>
          {isAdmin ? (
            <button
              type="button"
              className={activePage === 'admin' ? 'nav-btn active' : 'nav-btn'}
              onClick={openAdminPanel}
            >
              Admin
            </button>
          ) : null}
        </nav>

        <div className="user-chip">
          <UserButton afterSignOutUrl="/" />
          <span>{user?.firstName || user?.emailAddresses?.[0]?.emailAddress}</span>
        </div>
      </header>

      {activePage === 'feed'
        ? renderFeedPage()
        : activePage === 'search'
          ? renderSearchPage()
        : activePage === 'post-video'
          ? renderPostVideoPage()
          : activePage === 'admin'
            ? renderAdminPage()
            : renderDetailPage()}
    </main>
  )
}

export default App
