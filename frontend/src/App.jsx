import { SignIn, UserButton, useUser } from '@clerk/react'
import './App.css'

function App() {
  const { isLoaded, isSignedIn, user } = useUser()

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

  return (
    <main className="screen app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">CaughtOnDash</span>
          <h1>Dashboard</h1>
        </div>
        <div className="user-chip">
          <UserButton afterSignOutUrl="/" />
          <span>{user?.firstName || user?.emailAddresses?.[0]?.emailAddress}</span>
        </div>
      </header>

      <section className="hero-card">
        <div>
          <p className="eyebrow">Signed in</p>
          <h2>Welcome back{user?.firstName ? `, ${user.firstName}` : ''}.</h2>
          <p className="hero-text">
            You are now on the main page. This is where the logged-in
            experience can live.
          </p>
        </div>

        <div className="stats-grid">
          <article>
            <span>Session</span>
            <strong>Active</strong>
          </article>
          <article>
            <span>Access</span>
            <strong>Granted</strong>
          </article>
          <article>
            <span>Mode</span>
            <strong>Clerk</strong>
          </article>
        </div>
      </section>
    </main>
  )
}

export default App
