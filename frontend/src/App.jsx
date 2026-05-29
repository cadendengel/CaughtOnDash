import { useEffect, useRef, useState } from 'react'
import { SignIn, UserButton, useUser } from '@clerk/react'
import './App.css'

function App() {
  const { isLoaded, isSignedIn, user } = useUser()
  const [activePage, setActivePage] = useState('feed')
  const [posts, setPosts] = useState([])
  const [commentsByPostId, setCommentsByPostId] = useState({})
  const [commentsVisibleByPostId, setCommentsVisibleByPostId] = useState({})
  const [commentDraftsByPostId, setCommentDraftsByPostId] = useState({})
  const [loadingCommentsByPostId, setLoadingCommentsByPostId] = useState({})
  const [likeLoadingByPostId, setLikeLoadingByPostId] = useState({})
  const [commentLoadingByPostId, setCommentLoadingByPostId] = useState({})
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [uploadSuccess, setUploadSuccess] = useState('')
  const [uploading, setUploading] = useState(false)
  const viewedVideoIdsRef = useRef(new Set())

  const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

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

  const clerkEmail = user?.primaryEmailAddress?.emailAddress || user?.emailAddresses?.[0]?.emailAddress || ''
  const clerkUsername = user?.username || clerkEmail.split('@')[0] || ''
  const clerkDisplayName =
    [user?.firstName, user?.lastName].filter(Boolean).join(' ').trim() ||
    user?.fullName ||
    clerkUsername ||
    'Dash User'

  const identityPayload = {
    clerk_user_id: user?.id,
    email: clerkEmail,
    username: clerkUsername,
    display_name: clerkDisplayName,
    avatar_url: user?.imageUrl || '',
  }

  const getVideoDurationSeconds = (file) => {
    return new Promise((resolve) => {
      const videoElement = document.createElement('video')
      const objectUrl = URL.createObjectURL(file)

      const cleanup = () => {
        URL.revokeObjectURL(objectUrl)
      }

      videoElement.preload = 'metadata'
      videoElement.onloadedmetadata = () => {
        const duration = Number.isFinite(videoElement.duration)
          ? Math.max(0, Math.round(videoElement.duration))
          : 0
        cleanup()
        resolve(duration)
      }
      videoElement.onerror = () => {
        cleanup()
        resolve(0)
      }
      videoElement.src = objectUrl
    })
  }

  const markVideoViewed = async (videoId) => {
    if (!videoId || viewedVideoIdsRef.current.has(videoId)) {
      return
    }

    viewedVideoIdsRef.current.add(videoId)

    try {
      await fetch(`${API_BASE}/api/videos/${videoId}/view/`, {
        method: 'POST',
      })
    } catch (err) {
      // ignore view-count failures for now
    }
  }

  const loadFeed = async () => {
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      const res = await fetch(`${API_BASE}/api/feed/`, { headers })
      if (!res.ok) return
      const data = await res.json()
      setPosts(data.items || [])
    } catch (err) {
      // ignore for now
    }
  }

  const loadComments = async (videoId) => {
    if (!videoId) {
      return
    }

    setLoadingCommentsByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/videos/${videoId}/comments/`)
      if (!res.ok) return
      const data = await res.json()
      setCommentsByPostId((current) => ({ ...current, [videoId]: data.items || [] }))
    } catch (err) {
      // ignore for now
    } finally {
      setLoadingCommentsByPostId((current) => ({ ...current, [videoId]: false }))
    }
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

  const toggleLike = async (videoId) => {
    if (!videoId || likeLoadingByPostId[videoId]) {
      return
    }

    setLikeLoadingByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const response = await fetch(`${API_BASE}/api/videos/${videoId}/like/`, {
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
      const result = data.video || {}
      setPosts((current) =>
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
      const response = await fetch(`${API_BASE}/api/videos/${videoId}/comments/`, {
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

      const data = await response.json()
      const comment = data.comment
      setCommentsByPostId((current) => ({
        ...current,
        [videoId]: [...(current[videoId] || []), comment],
      }))
      setPosts((current) =>
        current.map((post) =>
          post.id === videoId
            ? {
                ...post,
                comments_count: (post.comments_count || 0) + 1,
              }
            : post,
        ),
      )
      setCommentDraftsByPostId((current) => ({ ...current, [videoId]: '' }))
      setCommentsVisibleByPostId((current) => ({ ...current, [videoId]: true }))
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
      await fetch(`${API_BASE}/api/auth/bootstrap/`, {
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
      await loadFeed()
    }

    syncAndLoad()
  }, [isSignedIn, user?.id])

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
          <span className="author-name">{post.display_name || post.username || post.owner_clerk_user_id}</span>
          <span className="author-handle">@{post.username || post.owner_clerk_user_id}</span>
        </div>
        <span className="timestamp">{formatTimestamp(post.created_at)}</span>
      </div>

      <h2>{post.title}</h2>
      <p>{post.description}</p>

      {post.playback_url ? (
        <video
          className="feed-video"
          controls
          playsInline
          preload="metadata"
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

      <div className="video-meta">
        <span>{post.duration_seconds ? `${post.duration_seconds}s` : 'Duration unavailable'}</span>
        <span>{post.views} views</span>
        <span>{post.likes_count || 0} likes</span>
        <span>{post.comments_count || 0} comments</span>
      </div>

      <div className="post-actions">
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
          onClick={() => toggleComments(post.id)}
        >
          {commentsVisibleByPostId[post.id]
            ? 'Hide comments'
            : `Comments · ${post.comments_count || 0}`}
        </button>
      </div>

      {commentsVisibleByPostId[post.id] ? (
        <div className="comments-panel">
          {loadingCommentsByPostId[post.id] ? (
            <p className="comments-empty">Loading comments...</p>
          ) : null}

          {!loadingCommentsByPostId[post.id] && (commentsByPostId[post.id] || []).length === 0 ? (
            <p className="comments-empty">No comments yet. Be the first to reply.</p>
          ) : null}

          <div className="comments-list">
            {(commentsByPostId[post.id] || []).map((comment) => (
              <article key={comment.id} className="comment-card">
                <div className="comment-head">
                  <div className="comment-author-block">
                    <span className="comment-author-name">{comment.display_name || comment.username}</span>
                    <span className="comment-author-handle">@{comment.username || comment.user_clerk_user_id}</span>
                  </div>
                  <span className="comment-timestamp">{formatTimestamp(comment.created_at)}</span>
                </div>
                <p>{comment.text}</p>
              </article>
            ))}
          </div>

          <form className="comment-form" onSubmit={(event) => handleCommentSubmit(post.id, event)}>
            <textarea
              className="comment-input"
              value={commentDraftsByPostId[post.id] || ''}
              onChange={(event) =>
                setCommentDraftsByPostId((current) => ({
                  ...current,
                  [post.id]: event.target.value,
                }))
              }
              rows="3"
              placeholder="Add a comment..."
            />
            <button type="submit" className="primary-btn" disabled={commentLoadingByPostId[post.id]}>
              {commentLoadingByPostId[post.id] ? 'Posting...' : 'Post comment'}
            </button>
          </form>
        </div>
      ) : null}
    </article>
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
      const durationSeconds = await getVideoDurationSeconds(uploadFile)

      const bootstrapResponse = await fetch(`${API_BASE}/api/videos/upload-url/`, {
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

      const uploadResponse = await fetch(`${API_BASE}/api/videos/upload/`, {
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
            className={activePage === 'post-video' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('post-video')}
          >
            Post Video
          </button>
        </nav>

        <div className="user-chip">
          <UserButton afterSignOutUrl="/" />
          <span>{user?.firstName || user?.emailAddresses?.[0]?.emailAddress}</span>
        </div>
      </header>

      {activePage === 'feed' ? renderFeedPage() : renderPostVideoPage()}
    </main>
  )
}

export default App
