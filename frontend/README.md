# Frontend — Aivora Gatekeeper Dashboard

Ultra-simple one-screen React dashboard for monitoring and controlling the Aivora Gatekeeper API protection engine.

## Quick Start

```bash
cd frontend

# Install dependencies
npm install

# Start development server (hot-reload)
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## Build for Production

```bash
npm run build
```

Output is written to `dist/`. Serve with any static host (Nginx, Vercel, Netlify, etc.):

```bash
# Preview the production build locally
npm run preview
```

---

## Environment Variables

Create a `.env` file in `frontend/` to point at a non-default backend:

```env
# .env
VITE_API_URL=http://localhost:8000
```

| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | Base URL of the FastAPI gatekeeper backend |

If the backend is unreachable, the UI falls back to demo data automatically — no configuration needed for offline development.

---

## UI Overview

The dashboard is intentionally one screen with three zones:

```
+-- Header --------------------------------------------------+
|  Shield  Aivora Gatekeeper              Updated 2:45:00 PM |
+------------------------------------------------------------+
|                                                            |
|                   STOPPED (red)                            |
|                   Bill at Risk                             |
|                                                            |
|          [ Activate 1-Click Shield ]                       |
|                                                            |
|   +------------+ +------------+ +----------------------+   |
|   |    $1,247  | |   3,842    | |         892          |   |
|   | Money Saved| |Active Users| |  Blocked Spammers    |   |
|   +------------+ +------------+ +----------------------+   |
|                                                            |
+-- Footer --------------------------------------------------+
|  Circuit Breaker: CLOSED                          v1.0.0  |
+------------------------------------------------------------+
```

After pressing **Activate 1-Click Shield**, the status flips to `PROTECTED (Safe)` and the button changes to **Fix & Rotate API Keys**.

---

## Accessibility

- All interactive elements have `aria-label` descriptions
- Status region uses `aria-live="polite"` for screen reader announcements
- Primary button minimum touch target: 80 px tall, full-width up to 28 rem
- High contrast: white text on dark slate backgrounds (WCAG AA compliant)
- Keyboard navigable — Tab, Enter, Space all work correctly
- Skip-to-main-content link appears on first Tab press

---

## Tech Stack

| Tool | Version |
|---|---|
| React | 19 |
| TypeScript | 6 |
| Vite | 8 |
| Tailwind CSS | 4 (via `@tailwindcss/vite`) |

---

## Backend Connection

The dashboard polls `GET /v1/gatekeeper/status` every 30 seconds and calls `POST /v1/gatekeeper/protect` when the action button is pressed. See `src/api.ts` for the full integration.

Run the backend first:

```bash
cd ../backend
uvicorn app.main:app --reload --port 8000
```
