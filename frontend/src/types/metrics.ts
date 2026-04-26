// Shape of one app's metrics as pushed by the WebSocket every 100ms
export interface AppMetrics {
  name: string;
  throughput_mbps: number;
  latency_ms: number;
  packet_loss_pct: number;
  weight: number;
  tokens_available: number;
  timestamp: number;
}

// Full WebSocket payload — keyed by app name
export type MetricsPayload = Record<string, AppMetrics>;

// One data point stored in history (with a JS Date ms timestamp for Recharts)
export interface MetricsPoint {
  t: number; // ms since epoch
  throughput_mbps: number;
  latency_ms: number;
  packet_loss_pct: number;
  tokens_available: number;
}

// Per-app rolling history + latest snapshot
export interface AppState {
  latest: AppMetrics;
  history: MetricsPoint[]; // capped at 600 points (~60s)
}

// /processes endpoint response item (all processes)
export interface ProcessEntry {
  pid: number;
  name: string;
  status: string;
  cpu_percent: number;
  memory_mb: number;
}

// /network-processes endpoint — only processes with active inet connections.
// Multiple PIDs with the same name are collapsed into one entry (e.g. chrome.exe).
export interface NetworkProcessEntry {
  name: string;       // e.g. 'chrome.exe'
  pid: number;        // lowest PID in the group (representative)
  pid_count: number;  // total PIDs sharing this name on the network
  status: string;
  memory_mb: number;  // summed RSS across all PIDs in the group
  bytes_sent: number; // cumulative IO read bytes (activity proxy)
  bytes_recv: number; // cumulative IO write bytes (activity proxy)
}

// /status endpoint response
export interface BackendStatus {
  mode: 'sim' | 'real';
  total_bandwidth_mbps?: number;  // actual key returned by main.py
  bandwidth_mbps?: number;        // defensive fallback
  bandwidth?: number;             // defensive fallback
  apps?: MetricsPayload;          // actual key returned by main.py
  metrics?: MetricsPayload;       // defensive fallback
}

export type AppMode = 'sim' | 'real';
