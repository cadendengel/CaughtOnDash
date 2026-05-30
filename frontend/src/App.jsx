import { useEffect, useRef, useState } from 'react'
import { SignIn, UserButton, useUser } from '@clerk/react'
import './App.css'
import './VideoDetail.css'

function App() {
  const { isLoaded, isSignedIn, user } = useUser()
  const [activePage, setActivePage] = useState('feed')
  const [isAdmin, setIsAdmin] = useState(false)
  const [posts, setPosts] = useState([])
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
  const [shareStatusByPostId, setShareStatusByPostId] = useState({})

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
      const items = data.items || []
      setPosts(items)
      return items
    } catch (err) {
      // ignore for now
    }
    return []
  }

  const loadComments = async (videoId) => {
    if (!videoId) {
      return
    }

    setLoadingCommentsByPostId((current) => ({ ...current, [videoId]: true }))
    try {
      const headers = user?.id ? { 'X-Clerk-User-Id': user.id } : {}
      const res = await fetch(`${API_BASE}/api/videos/${videoId}/comments/`, { headers })
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
    await Promise.all(items.map((post) => loadComments(post.id)))
  }

  const loadCurrentUserSummary = async () => {
    if (!user?.id) {
      setIsAdmin(false)
      return
    }

    try {
      const res = await fetch(`${API_BASE}/api/auth/me/`, {
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
      const res = await fetch(`${API_BASE}/api/videos/${videoId}/`, { headers })
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
    setActivePage('feed')
    setCurrentVideo(null)
    if (typeof window !== 'undefined') {
      window.history.pushState({}, '', window.location.pathname)
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
      const response = await fetch(`${API_BASE}/api/videos/comments/${commentId}/like/`, {
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
      const response = await fetch(`${API_BASE}/api/videos/${videoId}/comments/`, {
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
      const result = data.video || (data.payload && data.payload.video) || {}
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
      setCurrentVideo((current) =>
        current && current.id === videoId
          ? {
              ...current,
              likes_count: result.likes_count ?? current.likes_count,
              liked: result.liked ?? current.liked,
            }
          : current,
      )
    } catch (err) {
      // ignore for now
    } finally {
      setLikeLoadingByPostId((current) => ({ ...current, [videoId]: false }))
    }
  }

  const deleteAdminVideo = async (videoId) => {
    if (!isAdmin || !videoId) {
      return
    }

    try {
      const response = await fetch(`${API_BASE}/api/videos/admin/videos/${videoId}/`, {
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
      const response = await fetch(`${API_BASE}/api/videos/admin/comments/${commentId}/`, {
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
          <span className="author-name">{post.display_name || post.username || post.owner_clerk_user_id}</span>
          <span className="author-handle">@{post.username || post.owner_clerk_user_id}</span>
        </div>
        <span className="timestamp">{formatTimestamp(post.created_at)}</span>
      </div>

      <h2>{post.title}</h2>

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
      </div>
    </article>
  )

  const renderCommentNode = (comment, videoId, depth = 0, showAdminActions = false) => {
    const handle = comment.username || comment.user_clerk_user_id
    const isReply = depth > 0

    return (
      <article key={comment.id} className={isReply ? 'comment-card comment-reply-card' : 'comment-card'}>
        <div className="comment-head">
          <div className="comment-author-block">
            <span className="comment-author-name">{comment.display_name || comment.username}</span>
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
                  <span className="author-name">{post.display_name || post.username || post.owner_clerk_user_id}</span>
                  <span className="author-handle">@{post.username || post.owner_clerk_user_id}</span>
                </div>
                <span className="timestamp">{formatTimestamp(post.created_at)}</span>
              </div>

              <h2>{post.title}</h2>

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
          <h2>{video.title}</h2>
        </div>

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
        </div>

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
        : activePage === 'post-video'
          ? renderPostVideoPage()
          : activePage === 'admin'
            ? renderAdminPage()
            : renderDetailPage()}
    </main>
  )
}

export default App
