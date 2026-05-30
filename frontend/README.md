# CaughtOnDash Frontend

CaughtOnDash is a dashcam-focused community app built with React, Vite, and Clerk. This frontend powers the feed, video upload flow, video detail page, threaded comments, likes, replies, and the admin moderation view.

## What It Does

- Browse a feed of community uploads with inline video playback.
- Open a dedicated detail page for each video.
- Like videos, like comments, reply to comments, and post new comments.
- Upload new dashcam clips with title, description, and custom tags.
- Show source-colored tags under each post title and author block.
- Surface an admin-only moderation view for deleting posts, comments, and replies.

## Tech Stack

- React 19
- Vite
- Clerk authentication
- Custom CSS for the app shell, feed cards, detail page, and moderation UI
- Django backend API at `VITE_API_BASE` or `http://localhost:8000`

## Local Development

Install dependencies:

```bash
npm install
```

Start the dev server:

```bash
npm run dev
```

Build for production:

```bash
npm run build
```

Preview the production build:

```bash
npm run preview
```

## Environment

Set `VITE_API_BASE` to the backend base URL if you are not running locally.

Example:

```bash
VITE_API_BASE=http://localhost:8000
```

## Notes

- The app expects Clerk user identity to be available in the browser.
- The backend currently provides the video, comment, like, tag, and admin moderation APIs used here.
- Tags are source-aware: user tags are blue, admin tags are red, and the schema is ready for future AI tags.

## AI Roadmap

The next major step for CaughtOnDash is AI-assisted video analysis. The long-term goal is to automatically review uploaded dashcam footage and generate useful metadata such as incident tags, scene context, and safety-related signals. That AI layer is not implemented yet, but the current tag system and source-aware UI are designed so AI-generated tags can be added later without redesigning the app.
