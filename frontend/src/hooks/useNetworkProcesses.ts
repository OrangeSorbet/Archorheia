import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api/http';
import type { NetworkProcessEntry } from '../types/metrics';

const REFRESH_MS = 3000;

/**
 * Fetches /network-processes immediately on mount, then re-fetches every 3s.
 * Designed for the Real-mode Add App modal — call inside the modal so the
 * interval is automatically cleared when the modal unmounts.
 *
 * Returns:
 *   processes  - current list, sorted by IO activity descending (from backend)
 *   loading    - true only on the first fetch (subsequent refreshes are silent)
 *   error      - last error message, or null
 *   lastUpdate - Date of last successful fetch (for the "live" indicator)
 */
export function useNetworkProcesses() {
  const [processes, setProcesses] = useState<NetworkProcessEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const firstFetch = useRef(true);

  const fetchOnce = useCallback(async () => {
    // Only show the loading spinner on the very first fetch.
    // Subsequent interval refreshes update silently so the list doesn't flicker.
    if (firstFetch.current) setLoading(true);
    try {
      const data = await api.getNetworkProcesses();
      setProcesses(data);
      setLastUpdate(new Date());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (firstFetch.current) {
        setLoading(false);
        firstFetch.current = false;
      }
    }
  }, []);

  useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchOnce]);

  return { processes, loading, error, lastUpdate };
}
