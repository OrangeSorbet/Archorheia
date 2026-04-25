import { useState, useCallback } from 'react';
import { api } from '../api/http';
import type { ProcessEntry } from '../types/metrics';

export function useProcesses() {
  const [processes, setProcesses] = useState<ProcessEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getProcesses();
      setProcesses(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  return { processes, loading, error, fetch };
}
