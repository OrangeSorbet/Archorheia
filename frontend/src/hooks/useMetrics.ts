import { useEffect, useRef, useState, useCallback } from 'react';
import type { AppState, MetricsPayload, MetricsPoint } from '../types/metrics';

const WS_URL = 'ws://localhost:8000/ws/metrics';
const MAX_HISTORY = 600;
const RECONNECT_BASE_MS = 3000;

export type MetricsMap = Record<string, AppState>;

export function useMetrics() {
  const [apps, setApps] = useState<MetricsMap>({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      attemptRef.current = 0;
    };

    ws.onmessage = (ev: MessageEvent) => {
      try {
        const payload: MetricsPayload = JSON.parse(ev.data as string);

        setApps((prev) => {
          const next: MetricsMap = { ...prev };

          for (const name of Object.keys(payload)) {
            const latest = payload[name];
            const point: MetricsPoint = {
              t: Date.now(),
              throughput_mbps: latest.throughput_mbps,
              latency_ms: latest.latency_ms,
              packet_loss_pct: latest.packet_loss_pct,
              tokens_available: latest.tokens_available,
            };

            const existing = prev[name];
            const history = existing
              ? [...existing.history.slice(-(MAX_HISTORY - 1)), point]
              : [point];

            next[name] = { latest, history };
          }

          // Remove apps that disappeared from the payload
          for (const name of Object.keys(prev)) {
            if (!payload[name]) delete next[name];
          }

          return next;
        });
      } catch {
        // malformed frame — ignore
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      const delay = Math.min(
        RECONNECT_BASE_MS * 2 ** attemptRef.current,
        30000
      );
      attemptRef.current += 1;
      reconnectTimer.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { apps, connected };
}
