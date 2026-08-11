# Flight Connection Probability frontend

A single-page Next.js + TypeScript interface for the local FastAPI service. It presents quantitative connection probabilities, historical evidence, scenario sensitivity, and modeling assumptions without qualitative risk labels.

## Local development

Start the FastAPI server from the repository root first:

```powershell
.venv\Scripts\python -m uvicorn backend.flight_connection.api:app --reload
```

Then start this app:

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

## Environment

`NEXT_PUBLIC_API_BASE_URL` is the browser-visible FastAPI origin and defaults to `http://127.0.0.1:8000`. Do not place secrets in this variable; values prefixed with `NEXT_PUBLIC_` are included in browser code.

## Verification

```powershell
npm test
npm run lint
npm run build
```

The tests mock only the HTTP boundary. The UI does not use mock or fallback estimates during normal operation.
