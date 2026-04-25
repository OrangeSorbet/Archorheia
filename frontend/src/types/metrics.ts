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

// /processes endpoint response item
export interface ProcessEntry {
  pid: number;
  name: string;
  status: string;
  cpu_percent: number;
  memory_mb: number;
}

// /status endpoint response
// The backend may return bandwidth as either key — handle both defensively
export interface BackendStatus {
  mode: 'sim' | 'real';
  bandwidth_mbps?: number;
  bandwidth?: number;        // fallback if backend uses this key
  metrics: MetricsPayload;
}

export type AppMode = 'sim' | 'real';
