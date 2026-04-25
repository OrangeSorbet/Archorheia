import { useEffect, useState } from 'react';
import { useProcesses } from '../hooks/useProcesses';
import { api } from '../api/http';
import type { AppMode } from '../types/metrics';

interface Props {
  mode: AppMode;
  onClose: () => void;
  onAdded: (name: string) => void;
}

export function AddAppModal({ mode, onClose, onAdded }: Props) {
  const { processes, loading, error, fetch } = useProcesses();
  const [query, setQuery] = useState('');
  const [weight, setWeight] = useState(5);
  const [selected, setSelected] = useState<string | null>(null);
  const [simName, setSimName] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  useEffect(() => {
    if (mode === 'real') fetch();
  }, [mode, fetch]);

  const filtered = processes.filter((p) =>
    p.name.toLowerCase().includes(query.toLowerCase())
  );

  const handleAdd = async () => {
    const name = mode === 'real' ? selected : simName.trim();
    if (!name) return;
    setAdding(true);
    setAddError(null);
    try {
      if (mode === 'real') {
        await api.addProcess(name, weight);
      } else {
        await api.addSimApp(name, weight);
      }
      onAdded(name);
      onClose();
    } catch (e) {
      setAddError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">
            {mode === 'real' ? 'Track Process' : 'Add Sim App'}
          </h2>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>

        {mode === 'real' ? (
          <>
            <input
              className="modal-search"
              placeholder="Filter processes…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            <div className="process-list">
              {loading && <div className="process-status">Loading…</div>}
              {error && <div className="process-status error">{error}</div>}
              {!loading &&
                filtered.map((p) => (
                  <div
                    key={p.pid}
                    className={`process-row ${selected === p.name ? 'selected' : ''}`}
                    onClick={() => setSelected(p.name)}
                  >
                    <span className="proc-name">{p.name}</span>
                    <span className="proc-meta">
                      PID {p.pid} · {p.memory_mb.toFixed(0)} MB
                    </span>
                  </div>
                ))}
            </div>
          </>
        ) : (
          <div className="sim-name-row">
            <input
              className="modal-search"
              placeholder="App name (e.g. chrome.exe)"
              value={simName}
              onChange={(e) => setSimName(e.target.value)}
              autoFocus
            />
          </div>
        )}

        <div className="modal-footer">
          <div className="weight-row">
            <label className="weight-label">
              Initial weight: <strong>{weight}</strong>
            </label>
            <input
              type="range"
              min={1}
              max={10}
              value={weight}
              onChange={(e) => setWeight(parseInt(e.target.value, 10))}
              className="slider modal-slider"
            />
          </div>
          {addError && <div className="modal-error">{addError}</div>}
          <div className="modal-actions">
            <button className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={handleAdd}
              disabled={
                adding ||
                (mode === 'real' ? !selected : !simName.trim())
              }
            >
              {adding ? 'Adding…' : 'Add'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
