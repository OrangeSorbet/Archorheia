import { useRef, useState } from 'react';
import { api } from '../api/http';
import type { AppMode } from '../types/metrics';

interface Props {
  mode: AppMode;
  bandwidth: number;
  connected: boolean;
  onModeChange: (m: AppMode) => void;
  onBandwidthChange: (mbps: number) => void;
  onAddApp: () => void;
}

export function GlobalControls({
  mode,
  bandwidth,
  connected,
  onModeChange,
  onBandwidthChange,
  onAddApp,
}: Props) {
  const [bwInput, setBwInput] = useState(bandwidth.toString());
  const [bwUnit, setBwUnit] = useState('mbps');
  const bwTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleModeToggle = async () => {
    const next: AppMode = mode === 'sim' ? 'real' : 'sim';
    try {
      await api.setMode(next);
      onModeChange(next);
    } catch {
      /* show nothing — connection error */
    }
  };

  const handleBwChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setBwInput(e.target.value);
    const val = parseFloat(e.target.value);
    if (isNaN(val) || val <= 0) return;
    if (bwTimer.current) clearTimeout(bwTimer.current);
    bwTimer.current = setTimeout(async () => {
      try {
        await api.setBandwidth(val, bwUnit);
        const mbps = val * ({ kbps:0.001, mbps:1, gbps:1000, kbs:0.008, mbs:8, gbs:8000 }[bwUnit] ?? 1);
        onBandwidthChange(mbps);
      } catch {
        /* ignore */
      }
    }, 500);
  };

  return (
    <header className="global-header">
      <div className="header-brand">
        <span className="brand-icon">⬡</span>
        <span className="brand-name">Archorheia</span>
      </div>

      <div className="header-controls">
        {/* Mode toggle */}
        <div className="mode-toggle">
          <button
            className={`mode-btn ${mode === 'sim' ? 'active' : ''}`}
            onClick={() => mode !== 'sim' && handleModeToggle()}
          >
            Sim
          </button>
          <button
            className={`mode-btn ${mode === 'real' ? 'active' : ''}`}
            onClick={() => mode !== 'real' && handleModeToggle()}
          >
            Real
          </button>
        </div>

        {/* Bandwidth */}
        <div className="bw-control">
          <label className="bw-label">Pipe</label>
          <input
            type="number"
            className="bw-input"
            value={bwInput}
            onChange={handleBwChange}
            min={1}
            step={10}
          />
          <select
            className="bw-unit-select"
            value={bwUnit}
            onChange={(e) => setBwUnit(e.target.value)}
          >
            <option value="kbps">Kbps</option>
            <option value="mbps">Mbps</option>
            <option value="gbps">Gbps</option>
            <option value="kbs">KB/s</option>
            <option value="mbs">MB/s</option>
            <option value="gbs">GB/s</option>
          </select>
        </div>

        {/* Add app */}
        <button className="btn-add" onClick={onAddApp}>
          + Add App
        </button>
      </div>

      {/* Connection status dot */}
      <div className="ws-status">
        <span
          className={`ws-dot ${connected ? 'ws-online' : 'ws-offline'}`}
        />
        <span className="ws-label">{connected ? 'Live' : 'Disconnected'}</span>
      </div>
    </header>
  );
}
