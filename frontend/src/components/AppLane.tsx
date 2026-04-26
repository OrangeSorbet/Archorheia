import { useRef, useState } from 'react';
import type { AppState } from '../types/metrics';
import { ThroughputChart } from './ThroughputChart';
import { LatencyChart } from './LatencyChart';
import { TokenGauge } from './TokenGauge';
import { WeightSlider } from './WeightSlider';
import { api } from '../api/http';

interface Props {
  appState: AppState;
  color: string;
  onRemove: (name: string) => void;
}

// Map a process name to a short display label
function shortName(name: string) {
  return name.replace(/\.exe$/i, '');
}

export function AppLane({ appState, color, onRemove }: Props) {
  const { latest, history } = appState;
  const [rlValue, setRlValue] = useState('');
  const [rlUnit, setRlUnit] = useState('mbs');
  const rlTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleRlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setRlValue(e.target.value);
    const val = parseFloat(e.target.value);
    if (isNaN(val) || val <= 0) return;
    if (rlTimer.current) clearTimeout(rlTimer.current);
    rlTimer.current = setTimeout(async () => {
      try { await api.setRateLimit(latest.name, val, rlUnit); } catch {}
    }, 400);
  };

  const handleRlUnitChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setRlUnit(e.target.value);
    const val = parseFloat(rlValue);
    if (isNaN(val) || val <= 0) return;
    if (rlTimer.current) clearTimeout(rlTimer.current);
    rlTimer.current = setTimeout(async () => {
      try { await api.setRateLimit(latest.name, val, e.target.value); } catch {}
    }, 400);
  };

  // No /remove-process endpoint in Phase 1–3 backend — hide locally.
  // When the backend adds it, swap onRemove to call api.removeProcess() first.
  const handleRemove = () => onRemove(latest.name);

  const lossColor =
    latest.packet_loss_pct > 5
      ? '#ef4444'
      : latest.packet_loss_pct > 1
      ? '#f59e0b'
      : 'var(--c-muted)';

  return (
    <div className="app-lane" style={{ '--lane-color': color } as React.CSSProperties}>
      <div className="lane-header">
        <div className="lane-title-group">
          <span className="lane-dot" style={{ background: color }} />
          <h3 className="lane-title">{shortName(latest.name)}</h3>
          <span className="lane-pid-label">{latest.name}</span>
          <div className="lane-rate-limit">
            <input
              type="number"
              className="rl-input"
              placeholder="∞"
              value={rlValue}
              onChange={handleRlChange}
              min={0.1}
              step={1}
            />
            <select className="rl-unit-select" value={rlUnit} onChange={handleRlUnitChange}>
              <option value="kbps">Kbps</option>
              <option value="mbps">Mbps</option>
              <option value="gbps">Gbps</option>
              <option value="kbs">KB/s</option>
              <option value="mbs">MB/s</option>
              <option value="gbs">GB/s</option>
            </select>
          </div>
        </div>
        <div className="lane-stats">
          <Stat label="↑" value={`${latest.throughput_mbps.toFixed(2)} Mbps`} />
          <Stat label="⏱" value={`${latest.latency_ms.toFixed(1)} ms`} />
          <Stat
            label="✗"
            value={`${latest.packet_loss_pct.toFixed(1)}%`}
            color={lossColor}
          />
        </div>
        <button
          className="remove-btn"
          onClick={handleRemove}
          title="Remove app"
        >
          ×
        </button>
      </div>

      <div className="lane-charts">
        <ThroughputChart history={history} color={color} />
        <LatencyChart history={history} color={color} />
      </div>

      <div className="lane-controls">
        <TokenGauge tokens={latest.tokens_available} color={color} />
        <WeightSlider name={latest.name} weight={latest.weight} color={color} />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value" style={color ? { color } : undefined}>
        {value}
      </span>
    </div>
  );
}
