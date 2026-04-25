# NetShaper — Full Project Context (Phases 1–4 Complete)

## What this project is

A Windows desktop application that intercepts real network packets per-process,
applies WFQ (Weighted Fair Queuing) + Token Bucket bandwidth shaping, and
displays a live React dashboard. Two modes:

- **Sim mode** — fake traffic generated in Python, same algorithm pipeline, no
  real interception. Safe to run without admin rights.
- **Real mode** — WinDivert intercepts actual OS packets. Requires Administrator.

---

## Tech stack

| Layer               | Technology                                       |
|---------------------|--------------------------------------------------|
| Packet interception | WinDivert + pydivert                             |
| Backend             | Python + FastAPI + uvicorn                       |
| Algorithm engine    | Pure Python (Token Bucket + WFQ from scratch)    |
| Frontend            | React 19 + Vite 8 + Recharts + custom CSS        |
| Desktop wrapper     | Electron (Phase 5 target)                        |
| Packaging           | PyInstaller + electron-builder → single .exe     |

---

## Folder structure

```
/app
  /backend
    /algorithms
      __init__.py           ← exports TokenBucket, WFQScheduler, TrafficGenerator
      token_bucket.py       ← TokenBucket class
      wfq.py                ← WFQScheduler class
      traffic_generator.py  ← TrafficGenerator class
    main.py                 ← FastAPI app, REST endpoints, WebSocket
    metrics_store.py        ← rolling 60s metrics history
    process_manager.py      ← psutil wrapper
    shaper.py               ← orchestration layer
    windivert_capture.py    ← real packet capture + re-injection
  /frontend                 ← Vite + React project root (Phase 4 complete)
    index.html
    vite.config.ts
    tsconfig.json
    package.json
    src/
      main.tsx
      App.tsx
      App.css
      vite-env.d.ts
      api/
        http.ts
      types/
        metrics.ts
      hooks/
        useMetrics.ts
        useProcesses.ts
      components/
        GlobalControls.tsx
        AppLane.tsx
        AddAppModal.tsx
        ThroughputChart.tsx
        LatencyChart.tsx
        TokenGauge.tsx
        WeightSlider.tsx
  /electron                 ← Phase 5 target (does not exist yet)
  /tests
  README.md
```

---

## Phase 1 — Algorithm engine (COMPLETE)

### `token_bucket.py` — `TokenBucket`

```python
TokenBucket(rate_bps: float, capacity: float = None, max_delay_ms: float = 500)
```

**Key method:**
```python
consume(packet_size_bytes: int) -> dict
# Returns {"action": "forward"|"delay"|"drop", "delay_ms": float, "tokens_left": float}
```

Other methods: `peek_tokens()`, `update_rate(rate_bps, capacity)`, `reset_stats()`

`TokenBucketStats` dataclass: `packets_forwarded`, `packets_dropped`,
`packets_delayed`, `bytes_forwarded`, `total_delay_ms`, `avg_delay_ms` (property),
`loss_percent` (property).

---

### `wfq.py` — `WFQScheduler`

```python
WFQScheduler(total_bandwidth_bps: float = 100e6)
```

Keyed by integer PID.

```python
add_process(pid: int, name: str, weight: float = 5.0) -> ProcessEntry
remove_process(pid: int)
set_weight(pid: int, weight: float)
set_total_bandwidth(bps: float)
consume(pid: int, packet_size_bytes: int) -> dict
snapshot() -> list[WFQSnapshot]   # resets byte counters each call
```

`WFQSnapshot` dataclass: `pid`, `name`, `weight`, `share_percent`,
`allocated_bps`, `throughput_bps`, `tokens_left`, `stats: TokenBucketStats`.

---

### `traffic_generator.py` — `TrafficGenerator`

```python
TrafficGenerator(scheduler: WFQScheduler)
add_sim_process(pid, name, weight, load_mbps)  # also calls scheduler.add_process()
remove_sim_process(pid)                         # also calls scheduler.remove_process()
start() / stop()
update_load(pid, load_mbps)
pop_events() -> list[dict]
```

**CRITICAL:** Never call `scheduler.add_process()` directly. Always go through
`traffic_gen.add_sim_process()`. Calling both causes `ValueError: PID already registered`.

---

## Phase 2 — FastAPI backend (COMPLETE)

Runs on `http://localhost:8000`. Start with `python main.py` from `/backend`.

### `metrics_store.py`

Rolling 60s history (600 data points at 100ms intervals per app).

```python
add_app(name, weight)
remove_app(name)
set_weight(name, weight)
record(name, throughput_mbps, latency_ms, packet_loss_pct, tokens_available)
snapshot() -> dict   # keyed by app name — what WebSocket broadcasts
```

**`snapshot()` return shape (WebSocket payload):**
```json
{
  "chrome.exe": {
    "throughput_mbps": 4.2,
    "latency_ms": 12.0,
    "packet_loss_pct": 0.0,
    "weight": 5,
    "tokens_available": 830,
    "name": "chrome.exe",
    "timestamp": 1234567890.0
  }
}
```

---

### `process_manager.py`

```python
get_all_processes() -> list[dict]       # [{pid, name, status, cpu_percent, memory_mb}]
pid_to_name(pid: int) -> str | None
name_to_pids(name: str) -> list[int]
port_to_pid(port: int, proto: str = "tcp") -> int | None
```

---

### `shaper.py`

Central orchestration layer. Fake PIDs start at 90000 (never collide with real OS PIDs).

**`_name_to_pid: Dict[str, int]`** — master name↔pid registry. WinDivertCapture
reads `shaper._name_to_pid.values()` and `shaper._pid_to_name` directly.

**Phase 3 additions to shaper.py:**
- `_register_real_pids(name)` — calls `process_manager.name_to_pids()`, adds all
  found real OS PIDs to `_pid_to_name`, removes stale ones. Handles Firefox-style
  multi-process apps.
- `process_packet(pid, size)` — translates real OS PID → name → fake PID before
  calling `scheduler.consume()`. Returns the result dict (Phase 2 discarded it —
  do not revert). If PID is stale, returns `{"action": "forward"}` gracefully.
- `run()` — refreshes real PIDs every 5 seconds automatically.

```python
add_app(name: str, weight: int = 1)
remove_app(name: str)
set_weight(name: str, weight: int)
process_packet(pid: int, size_bytes: int) -> dict   # MUST return the dict
list_apps() -> list
async run()    # background asyncio task, calls _tick() every 100ms
def _tick()    # scheduler.snapshot() → MetricsStore
def stop()
```

---

### `main.py` — REST endpoints

| Method | Path            | Body / Notes                               |
|--------|-----------------|--------------------------------------------|
| GET    | /processes      | Returns `list[{pid, name, status, cpu_percent, memory_mb}]` |
| POST   | /add-process    | `{name: str, weight: int 1–10}`            |
| POST   | /add-sim-app    | `{name: str, weight: int 1–10}`            |
| POST   | /set-weight     | `{name: str, weight: int 1–10}`            |
| POST   | /set-mode       | `{mode: "sim" | "real"}`                   |
| POST   | /set-bandwidth  | `{mbps: float > 0}`                        |
| GET    | /status         | Returns `{mode, bandwidth_mbps, metrics}`  |

**WebSocket:** `ws://localhost:8000/ws/metrics` — pushes `MetricsStore.snapshot()`
as JSON every 100ms.

**Note:** There is NO `/remove-process` endpoint in the backend yet. Removing an
app from the frontend only hides it locally (see Phase 4 `hidden` set in App.tsx).

---

## Phase 3 — WinDivert integration (COMPLETE)

### `windivert_capture.py` — `WinDivertCapture`

```python
WinDivertCapture(shaper: Shaper)
start()    # raises ImportError if pydivert missing, OSError if not Admin
stop()     # drains delay queue so connections don't stall
is_running: bool
```

**Packet flow:**
```
WinDivert recv() → _resolve_pid() → check shaper._pid_to_name
  → not tracked: wd.send() immediately
  → tracked: shaper.process_packet(pid, size) → action dict
      "forward" → wd.send() immediately
      "delay"   → heapq.heappush(_delay_queue, (send_at, packet))
      "drop"    → silent discard (TCP retransmits)

_reinjector_loop() wakes every 5ms, pops packets whose send_at <= now
```

**Key constants:**
```python
PORT_TABLE_REFRESH_MS = 500
REINJECTOR_POLL_MS    = 5
MAX_DELAY_QUEUE       = 2000
```

**PID resolution:** local port → `_port_pid_cache` (rebuilt from
`psutil.net_connections()` every 500ms). Per-packet rebuild is too expensive.

**Threading:** `windivert-capture` (daemon) + `windivert-reinjector` (daemon) +
`threading.Event(_stop_event)`.

---

## Phase 4 — React Frontend (COMPLETE)

### Setup

- **Framework:** React 19 + Vite 8
- **Charts:** Recharts
- **Styling:** Custom CSS (no Tailwind — pure CSS custom properties)
- **Fonts:** Space Grotesk (UI) + IBM Plex Mono (numbers/labels) via Google Fonts
- **Theme:** Dark industrial — `#0a0b0e` base, layered surfaces

### How to run

```bash
# Terminal 1
cd backend && python main.py         # FastAPI on :8000

# Terminal 2
cd frontend && npm run dev           # Vite on :5173
```

Vite proxies `/api/*` → `http://localhost:8000` (strips `/api` prefix).
WebSocket connects directly to `ws://localhost:8000/ws/metrics` (not proxied).

### npm dependencies added in Phase 4

```json
"dependencies": {
  "recharts": "^2.x"
},
"devDependencies": {
  "@types/recharts": "^1.x"
}
```

### CSS design tokens (all in `src/App.css` `:root`)

```css
--c-bg:       #0a0b0e;   /* page background */
--c-surface:  #111318;   /* cards, header */
--c-surface2: #181b21;   /* chart backgrounds, inner panels */
--c-border:   #1f2330;   /* default borders */
--c-border2:  #2a2f3e;   /* hover/focus borders */
--c-text:     #c9d1e0;   /* body text */
--c-muted:    #4a5266;   /* labels, secondary text */
--c-dim:      #2e3548;   /* disabled/empty state */
--font-ui:    'Space Grotesk', system-ui, sans-serif;
--font-mono:  'IBM Plex Mono', ui-monospace, monospace;
--radius:     8px;
--radius-lg:  12px;
--transition: 0.15s ease;
```

### App color palette (8-color, stable by name hash)

```ts
const PALETTE = [
  '#38bdf8', // sky
  '#a78bfa', // violet
  '#34d399', // emerald
  '#fb923c', // orange
  '#f472b6', // pink
  '#facc15', // amber
  '#22d3ee', // cyan
  '#f87171', // red
];
// Color assigned by: hash(appName) % 8 — stable across renders
```

---

### `src/types/metrics.ts`

```ts
interface AppMetrics {
  name: string;
  throughput_mbps: number;
  latency_ms: number;
  packet_loss_pct: number;
  weight: number;
  tokens_available: number;
  timestamp: number;
}

type MetricsPayload = Record<string, AppMetrics>;

interface MetricsPoint {
  t: number;                 // Date.now() ms — used as Recharts x-axis
  throughput_mbps: number;
  latency_ms: number;
  packet_loss_pct: number;
  tokens_available: number;
}

interface AppState {
  latest: AppMetrics;
  history: MetricsPoint[];  // capped at 600 points (~60s at 100ms push)
}

interface ProcessEntry {
  pid: number;
  name: string;
  status: string;
  cpu_percent: number;
  memory_mb: number;
}

interface BackendStatus {
  mode: 'sim' | 'real';
  bandwidth_mbps?: number;  // defensive: backend may use either key
  bandwidth?: number;
  metrics: MetricsPayload;
}

type AppMode = 'sim' | 'real';
```

---

### `src/api/http.ts`

All REST calls go through typed wrappers. Base path is `/api` (proxied by Vite).

```ts
export const api = {
  getProcesses: () => get<ProcessEntry[]>('/processes'),
  getStatus:    () => get<BackendStatus>('/status'),
  addProcess:   (name, weight) => post('/add-process', { name, weight }),
  addSimApp:    (name, weight) => post('/add-sim-app', { name, weight }),
  setWeight:    (name, weight) => post('/set-weight', { name, weight }),
  setMode:      (mode)         => post('/set-mode', { mode }),
  setBandwidth: (mbps)         => post('/set-bandwidth', { mbps }),
};
```

---

### `src/hooks/useMetrics.ts`

Manages the WebSocket connection lifecycle.

- Connects to `ws://localhost:8000/ws/metrics` directly (not via Vite proxy)
- Maintains `MetricsMap = Record<string, AppState>` in state
- Appends each 100ms push as a `MetricsPoint` to each app's `history[]`
- Caps history at `MAX_HISTORY = 600` points (slice from tail)
- Removes apps from state when they disappear from the WS payload
- Reconnects with exponential backoff on close: `3s * 2^attempt`, capped at 30s
- Returns `{ apps: MetricsMap, connected: boolean }`

---

### `src/hooks/useProcesses.ts`

```ts
useProcesses() → { processes: ProcessEntry[], loading, error, fetch }
// fetch() calls api.getProcesses() — called manually (not on mount)
// AddAppModal calls fetch() on open in Real mode
```

---

### `src/components/GlobalControls.tsx`

Sticky header bar, `z-index: 100`.

Props:
```ts
{ mode, bandwidth, connected, onModeChange, onBandwidthChange, onAddApp }
```

- **Mode toggle:** Sim | Real pill buttons. Calls `api.setMode()` on click, then
  `onModeChange()` on success. Switching modes is mutually exclusive.
- **Bandwidth input:** Number input, debounced 500ms, calls `api.setBandwidth()`.
- **Add App button:** Calls `onAddApp()` to open modal.
- **WS indicator:** Pulsing green dot when connected, grey when disconnected.

---

### `src/components/AppLane.tsx`

One card per tracked app. Props: `{ appState: AppState, color: string, onRemove }`.

Layout (top to bottom):
1. **Lane header** — colored dot, `.exe`-stripped title, full name label, live stats
   (↑ throughput, ⏱ latency, ✗ loss), × remove button
2. **Charts row** — `ThroughputChart` + `LatencyChart` side by side (2-col grid)
3. **Controls row** — `TokenGauge` + `WeightSlider` side by side (2-col grid)

CSS variable `--lane-color` set per lane drives the left border accent (3px solid).

Packet loss color logic:
- `> 5%` → `#ef4444` (red)
- `> 1%` → `#f59e0b` (amber)
- else  → `var(--c-muted)`

Remove button: hides app in local `hidden: Set<string>` state in `App.tsx`.
No backend call — there is no `/remove-process` endpoint yet.

---

### `src/components/ThroughputChart.tsx`

Recharts `AreaChart`. Props: `{ history: MetricsPoint[], color: string }`.

- Height: 90px, `isAnimationActive={false}` (critical — 100ms refresh rate)
- Gradient fill: `color` at 35% opacity fading to 2%
- Y-axis: auto-scaled, no axis line, small tick labels in `--c-muted`
- Tooltip: dark themed matching design tokens
- Data mapped as `{ t, v: throughput_mbps }`

---

### `src/components/LatencyChart.tsx`

Recharts `LineChart`. Same props and constraints as ThroughputChart.

- Height: 90px, `isAnimationActive={false}`
- Stroke only, no fill
- Data mapped as `{ t, v: latency_ms }`

---

### `src/components/TokenGauge.tsx`

Props: `{ tokens: number, max?: number (default 1000), color: string }`.

Horizontal fill bar. Color changes by health:
- `> 60%` → app's lane color
- `> 25%` → `#f59e0b` (amber warning)
- `≤ 25%` → `#ef4444` (red critical)

Width and color both transition smoothly (`0.15s`, `0.3s`).

---

### `src/components/WeightSlider.tsx`

Props: `{ name: string, weight: number, color: string }`.

- Local state `local` for immediate UI feedback
- `useEffect` syncs `local` when `weight` prop changes (external WS update)
- Debounced 300ms before calling `api.setWeight()`
- Shows "…" saving indicator while POST in flight
- Tick marks 1–10 below slider, active tick highlighted

---

### `src/components/AddAppModal.tsx`

Props: `{ mode: AppMode, onClose: () => void, onAdded: (name: string) => void }`.

**Real mode:** Fetches `/processes` on mount, shows searchable list with
`proc-name` (mono) + `PID · MB` meta. Click to select, then Add.

**Sim mode:** Free-text input for app name (e.g. `chrome.exe`).

Both modes: weight slider (1–10, initial value 5), error display, Cancel/Add buttons.

On success: calls `onAdded(name)` which unhides the app if it was previously
hidden, then closes modal.

---

### `src/App.tsx` — root component

```
useMetrics() → { apps, connected }
api.getStatus() on mount → sets initial mode + bandwidth
hidden: Set<string> — locally removed apps (persisted in React state only)
visibleApps = Object.entries(apps).filter(not in hidden)
```

**Layout:**
```
<GlobalControls>          ← sticky header
<main>
  if visibleApps.length === 0 → <EmptyState>
  else → <div.lanes-grid>
    {visibleApps.map → <AppLane>}
  </div>
{showModal && <AddAppModal>}
```

**lanes-grid CSS:**
```css
grid-template-columns: repeat(auto-fill, minmax(560px, 1fr));
gap: 16px;
```
Responsive — wraps to single column on narrow viewports.

---

### `vite.config.ts`

```ts
server: {
  port: 5173,
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  },
}
```

---

### `tsconfig.json` (Phase 4 version)

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "allowArbitraryExtensions": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

---

## Critical invariants (must never be violated)

1. **Never call `scheduler.add_process()` directly.** Always via
   `traffic_gen.add_sim_process()`. Double-call causes `ValueError` crash.

2. **`process_packet()` must return the dict.** WinDivertCapture reads the return
   value. Phase 2 discarded it — that was a bug, fixed in Phase 3. Do not revert.

3. **Sim and Real modes are mutually exclusive.** `set_mode()` fully stops
   the outgoing mode before starting new one.

4. **Fake PIDs start at 90000.** Real OS PIDs on Windows are always lower.
   No collision risk in Real mode.

5. **WinDivert requires Administrator.** `WinDivertCapture.start()` raises
   `OSError` if not elevated.

6. **All intercepted packets must be re-injected or connections stall.**
   `stop()` drains the delay queue via a fresh WinDivert handle.

7. **`isAnimationActive={false}` on all Recharts components.** Recharts
   re-animates on every data change. At 100ms WebSocket push rate, enabling
   animation causes severe jank.

8. **WebSocket connects to `:8000` directly, not through Vite proxy.**
   The Vite proxy only handles HTTP (`/api/*`). The WS URL is hardcoded
   as `ws://localhost:8000/ws/metrics` in `useMetrics.ts`.

9. **No `/remove-process` backend endpoint exists.** Removing an app in the
   frontend only adds its name to a `hidden: Set<string>` in `App.tsx` state.
   The app reappears if it starts sending WS data again and is unhidden. Phase 5
   should add a proper backend endpoint if remove needs to be persistent.

---

## Known gaps / things Phase 5 should address

- **No Electron shell.** The frontend runs as a plain Vite dev server. Phase 5
  needs to wrap it in Electron so it ships as a `.exe`. The Electron main process
  should also spawn the Python backend (`python main.py`) on startup and kill it
  on quit.

- **No `/remove-process` endpoint.** Adding one to `main.py` would call
  `shaper.remove_app(name)`. Frontend `AppLane` already has a comment marking
  where to add the `api.removeProcess()` call.

- **No persistent settings.** Mode and bandwidth reset to defaults on page
  reload. Phase 5 could persist these via Electron's `app.getPath('userData')`.

- **TokenGauge `max` is hardcoded at 1000.** The real max depends on the
  `capacity` set in `TokenBucket`. The backend would need to expose this per-app
  for the gauge to be accurate at non-default capacities.

- **No error toast/notification system.** API failures in `GlobalControls` and
  `WeightSlider` are silently swallowed. Phase 5 should add a lightweight
  notification layer.

- **`package.json` still has React 19 scaffold deps.** Verify `react` and
  `react-dom` versions match what's actually installed when setting up Electron.

---

## Phase 5 — Electron wrapper (TARGET)

Expected scope:

```
/electron
  main.js (or main.ts)    ← Electron main process
  preload.js              ← context bridge (if needed)
```

Key tasks:
1. `electron` + `electron-builder` setup in `package.json`
2. Main process spawns `python backend/main.py` as a child process on app ready
3. Main process kills the Python process on `app.before-quit`
4. `BrowserWindow` loads `http://localhost:5173` in dev, or loads the built
   Vite bundle (`dist/index.html`) in production
5. `electron-builder` config to bundle:
   - The Vite-built frontend (`dist/`)
   - The PyInstaller-built backend executable (`backend.exe`)
   - Into a single NSIS installer for Windows
6. Handle the case where the backend takes a few seconds to start — frontend
   shows "Connecting…" state already (the WS reconnect loop handles this
   gracefully with exponential backoff up to 30s)

The frontend requires **zero changes** for Electron — it is already
backend-agnostic and connects via localhost URLs.
