import { useState } from 'react';
import { useProcesses } from '../hooks/useProcesses';
import { useNetworkProcesses } from '../hooks/useNetworkProcesses';
import { api } from '../api/http';
import type { AppMode } from '../types/metrics';

interface Props {
  mode: AppMode;
  onClose: () => void;
  onAdded: (name: string) => void;
}

function fmtBytes(b: number): string {
  if (b === 0) return '—';
  if (b >= 1_073_741_824) return `${(b / 1_073_741_824).toFixed(1)} GB`;
  if (b >= 1_048_576)     return `${(b / 1_048_576).toFixed(1)} MB`;
  if (b >= 1_024)         return `${(b / 1_024).toFixed(0)} KB`;
  return `${b} B`;
}

function fmtMem(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(0)} MB`;
}

function fmtTime(d: Date): string {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function AddAppModal({ mode, onClose, onAdded }: Props) {
  const [query, setQuery]       = useState('');
  const [weight, setWeight]     = useState(5);
  const [selected, setSelected] = useState<string | null>(null);
  const [simName, setSimName]   = useState('');
  const [adding, setAdding]     = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const isReal = mode === 'real';

  // Real mode: live-refreshing network process list
  const {
    processes: netProcs,
    loading:   netLoading,
    error:     netError,
    lastUpdate,
  } = useNetworkProcesses();

  // Sim mode: full process list, fetched once
  const {
    processes: allProcs,
    loading:   allLoading,
    error:     allError,
    fetch:     fetchAll,
  } = useProcesses();

  // Fetch sim list once when modal opens in sim mode
  const [simFetched, setSimFetched] = useState(false);
  if (!isReal && !simFetched) {
    setSimFetched(true);
    fetchAll();
  }

  const filteredNet = netProcs.filter(p =>
    p.name.toLowerCase().includes(query.toLowerCase())
  );
  const filteredAll = allProcs.filter(p =>
    p.name.toLowerCase().includes(query.toLowerCase())
  );

  const handleAdd = async () => {
    const name = isReal ? selected : simName.trim();
    if (!name) return;
    setAdding(true);
    setAddError(null);
    try {
      if (isReal) {
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

  const canAdd = isReal ? !!selected : !!simName.trim();
  const maxActivity = filteredNet[0]
    ? filteredNet[0].bytes_sent + filteredNet[0].bytes_recv
    : 1;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <h2 className="modal-title">
              {isReal ? 'Track Network Process' : 'Add Sim App'}
            </h2>
            {isReal && (
              <div className="modal-live-badge">
                <span className="live-dot" />
                <span className="live-label">
                  {lastUpdate ? `Updated ${fmtTime(lastUpdate)}` : 'Connecting…'}
                </span>
              </div>
            )}
          </div>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {/* Search / name input */}
        <div className="modal-search-wrap">
          <span className="modal-search-icon">⌕</span>
          <input
            className="modal-search modal-search-icon-pad"
            placeholder={isReal ? 'Filter processes…' : 'App name (e.g. chrome.exe)'}
            value={isReal ? query : simName}
            onChange={e => {
              if (isReal) setQuery(e.target.value);
              else { setSimName(e.target.value); setSelected(null); }
            }}
            autoFocus
          />
          {isReal && query && (
            <button className="modal-search-clear" onClick={() => setQuery('')}>×</button>
          )}
        </div>

        {/* Real mode: network process table */}
        {isReal && (
          <div className="process-list">
            {!netLoading && filteredNet.length > 0 && (
              <div className="proc-header-row">
                <span className="ph-name">Process</span>
                <span className="ph-mem">Memory</span>
                <span className="ph-io">I/O Activity</span>
              </div>
            )}

            {netLoading && (
              <div className="process-status">
                <span className="modal-spinner" />
                Scanning active network connections…
              </div>
            )}

            {netError && (
              <div className="process-status error">
                <div>{netError}</div>
                <div style={{ fontSize: 11, marginTop: 4, opacity: 0.7 }}>
                  Tip: Real mode requires Administrator privileges.
                </div>
              </div>
            )}

            {!netLoading && !netError && filteredNet.length === 0 && (
              <div className="process-status">No network-active processes found.</div>
            )}

            {!netLoading && filteredNet.map(p => {
              const activity = p.bytes_sent + p.bytes_recv;
              const pct = maxActivity > 0 ? Math.min(100, (activity / maxActivity) * 100) : 0;
              const isSelected = selected === p.name;
              return (
                <div
                  key={p.name}
                  className={`process-row ${isSelected ? 'selected' : ''}`}
                  onClick={() => setSelected(isSelected ? null : p.name)}
                >
                  {/* Name + PID badge */}
                  <div className="proc-identity">
                    <span className="proc-name">{p.name}</span>
                    <span className="proc-pid-badge">
                      {p.pid_count > 1
                        ? `${p.pid_count} processes`
                        : `PID ${p.pid}`}
                    </span>
                  </div>

                  {/* Memory */}
                  <span className="proc-mem">{fmtMem(p.memory_mb)}</span>

                  {/* IO column: numbers + relative bar */}
                  <div className="proc-io-col">
                    <div className="proc-io-nums">
                      <span className="io-label io-up">↑ {fmtBytes(p.bytes_sent)}</span>
                      <span className="io-label io-dn">↓ {fmtBytes(p.bytes_recv)}</span>
                    </div>
                    <div className="activity-track">
                      <div
                        className="activity-fill"
                        style={{
                          width: `${pct}%`,
                          background: isSelected ? '#38bdf8' : 'var(--c-border2)',
                        }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Sim mode: full process list */}
        {!isReal && (
          <div className="process-list">
            {allLoading && (
              <div className="process-status">
                <span className="modal-spinner" /> Loading processes…
              </div>
            )}
            {allError && (
              <div className="process-status error">{allError}</div>
            )}
            {!allLoading && filteredAll.length === 0 && !allError && (
              <div className="process-status">No processes found.</div>
            )}
            {!allLoading && filteredAll.map(p => (
              <div
                key={p.pid}
                className={`process-row ${selected === p.name ? 'selected' : ''}`}
                onClick={() => { setSelected(p.name); setSimName(p.name); }}
              >
                <div className="proc-identity">
                  <span className="proc-name">{p.name}</span>
                  <span className="proc-pid-badge">PID {p.pid}</span>
                </div>
                <span className="proc-mem">{fmtMem(p.memory_mb)}</span>
                <span className="proc-status-badge">{p.status}</span>
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="modal-footer">
          <div className="weight-row">
            <label className="weight-label">
              Initial weight:
              <strong className="weight-num">{weight}</strong>
            </label>
            <input
              type="range"
              min={1} max={10}
              value={weight}
              onChange={e => setWeight(parseInt(e.target.value, 10))}
              className="slider modal-slider"
            />
            <div className="weight-scale">
              <span>Low</span>
              <span>High</span>
            </div>
          </div>

          {addError && <div className="modal-error">{addError}</div>}

          <div className="modal-actions">
            <div className="modal-selection-hint">
              {selected
                ? <><span className="hint-check">✓</span> {selected}</>
                : <span style={{ color: 'var(--c-muted)', fontSize: 12 }}>
                    {isReal ? 'Select a process above' : 'Enter or select a name above'}
                  </span>
              }
            </div>
            <div className="modal-btns">
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button
                className="btn-primary"
                onClick={handleAdd}
                disabled={adding || !canAdd}
              >
                {adding ? 'Adding…' : `Track ${selected ?? '…'}`}
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
