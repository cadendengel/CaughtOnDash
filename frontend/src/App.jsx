import { useMemo, useState } from 'react'
import { SignIn, UserButton, useUser } from '@clerk/react'
import './App.css'

function App() {
  const { isLoaded, isSignedIn, user } = useUser()
  const [activePage, setActivePage] = useState('feed')

  const posts = useMemo(() => {
    return []
  }, [])

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
        <span className="author">@{post.author.username}</span>
        <span className="timestamp">{post.createdAt}</span>
      </div>

      <h2>{post.title}</h2>
      <p>{post.description}</p>

      <div className="video-meta">
        <span>{post.video.duration}</span>
        <span>{post.video.views} views</span>
      </div>
    </article>
  )

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

      <div className="post-video-card">
        <h3>Upload Flow Coming Next</h3>
        <p>
          This placeholder page is ready for your upload form, file picker,
          and metadata fields.
        </p>
        <button
          type="button"
          className="secondary-btn"
          onClick={() => setActivePage('feed')}
        >
          Back to Feed
        </button>
      </div>
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
