import type { ProcessEntry, BackendStatus, AppMode } from '../types/metrics';

const BASE = '/api';

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST ${path} failed (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
  return res.json() as Promise<T>;
}

export const api = {
  getProcesses: () => get<ProcessEntry[]>('/processes'),

  getStatus: () => get<BackendStatus>('/status'),

  addProcess: (name: string, weight: number) =>
    post('/add-process', { name, weight }),

  addSimApp: (name: string, weight: number) =>
    post('/add-sim-app', { name, weight }),

  setWeight: (name: string, weight: number) =>
    post('/set-weight', { name, weight }),

  setMode: (mode: AppMode) => post('/set-mode', { mode }),

  setBandwidth: (mbps: number) => post('/set-bandwidth', { mbps }),
};
