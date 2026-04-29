# Zomato UTR Agent — Deployment Guide

## Repo structure
```
├── main.py          # FastAPI backend
├── Dockerfile       # Docker config for Railway
├── railway.json     # Railway config
├── requirements.txt # Python deps
└── frontend/        # React app (deploy separately on Vercel)
    └── ZomatoDashboard.jsx
```

---

## 1. Deploy Backend on Railway

1. Push this repo to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub repo
3. Select your repo → Railway auto-detects the Dockerfile
4. Click Deploy
5. Once deployed, go to Settings → Networking → Generate Domain
6. Copy your domain e.g. `https://zomato-utr-agent.up.railway.app`

---

## 2. Deploy Frontend on Vercel

1. Create a new React app:
   ```bash
   npx create-react-app zomato-dashboard
   cd zomato-dashboard
   ```

2. Replace `src/App.js` with:
   ```jsx
   import ZomatoDashboard from './ZomatoDashboard';
   export default function App() { return <ZomatoDashboard />; }
   ```

3. Copy `ZomatoDashboard.jsx` into `src/`

4. Update the API URL in `ZomatoDashboard.jsx`:
   ```js
   const API = "https://YOUR-RAILWAY-DOMAIN.up.railway.app/api";
   ```

5. Deploy to Vercel:
   ```bash
   npm install -g vercel
   vercel --prod
   ```

6. Vercel gives you a URL like `https://zomato-dashboard.vercel.app`

---

## 3. Share with customers

Send customers this link:
```
https://zomato-dashboard.vercel.app
```

They click "Connect Zomato", log in once, and data syncs automatically.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/start-session` | Start browser + noVNC + tunnel |
| GET | `/api/session-status` | Check login status |
| POST | `/api/confirm-login` | Save session after login |
| POST | `/api/start-download` | Kick off UTR download |
| GET | `/api/download-status` | Poll download progress |
| GET | `/api/data` | Get UTR data as JSON |
| GET | `/api/download-csv` | Download merged CSV |
| POST | `/api/stop` | Close browser |
| GET | `/api/health` | Health check |

---

## Notes

- Session is saved to `/app/zomato_session.json` — persists across restarts if Railway volume is mounted
- Downloads saved to `/app/zomato_downloads/`
- For persistent storage, add a Railway Volume mounted at `/app`
- One browser session at a time — for multi-tenant, run one Railway service per customer