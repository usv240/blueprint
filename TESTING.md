# BLUEPRINT — Test Suite

## Prerequisites

- Blueprint server running on port **8081** (so it doesn't conflict with other local servers on 8080):
  ```powershell
  cd blueprint
  uvicorn backend.main:app --port 8081
  ```
- From the project root, install Playwright browsers once:
  ```powershell
  npx playwright install chromium
  ```

---

## Python API Tests

Run from inside the `blueprint/` directory.

### Fast tests (no Gemini/pipeline calls) — ~6 seconds

```powershell
cd blueprint
python -m pytest tests/ -v -m "not slow"
```

Covers: `/api/health`, `/api/about`, `/api/stats`, `/api/recent`, input validation (422/400/404), watchlist CRUD.

### Slow/live tests (full 7-agent pipeline) — ~3 minutes per test

```powershell
cd blueprint
python -m pytest tests/ -v -m slow
```

Covers: POST `/api/analyze`, SSE streaming, report retrieval, share link, HTML export, Q&A chat, POST `/api/compare`.

### All tests

```powershell
cd blueprint
python -m pytest tests/ -v
```

---

## Playwright Browser Tests

Run from the **project root** (one level above `blueprint/`).

### Fast UI tests only (no live pipeline)

```powershell
npx playwright test tests/blueprint.spec.js --project=chromium --grep-invert "Full analysis report"
```

Covers: page load, demo presets, HIW modal (4 tabs), compare modal, validated examples, API endpoint checks, "nothing is hardcoded" intercept tests.

### Full suite including live analysis (~2 minutes)

```powershell
npx playwright test tests/blueprint.spec.js --project=chromium
```

### Headed mode (see the browser)

```powershell
npx playwright test tests/blueprint.spec.js --project=chromium --headed
```

### All three browsers

```powershell
npx playwright test tests/blueprint.spec.js
```

### View HTML report after a run

```powershell
npx playwright show-report
```

---

## npm scripts (from project root)

```powershell
npm run test:browser          # Chromium only
npm run test:browser:all      # All browsers
npm run test:browser:headed   # Headed Chromium
npm run test:api              # Fast Python tests
npm run test:api:slow         # Slow Python pipeline tests
npm run test:api:all          # All Python tests
npm run test:report           # Open Playwright HTML report
```

---

## Test count

| Suite | Count | Notes |
|---|---|---|
| Python fast | 63 | Health, about, stats, validation, watchlist |
| Python slow | 32 | Full pipeline, SSE, share, export, Q&A, compare |
| Browser (Playwright) | 34 | UI, modal, API intercepts, live report check |
| **Total** | **129** | |

---

## Notes

- The Blueprint server must be on port **8081** (change if 8080 is already in use)
- Slow tests each make real calls to Gemini API — rate-limit-aware, ~60s per test
- The live analysis `beforeAll` runs the pipeline once and shares the page across 9 serial assertions
- AI-generated sections (escape plan, debate verdict) are soft-checked — they may be absent if the Gemini model returns no data for a given run
