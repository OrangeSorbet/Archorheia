import type { AppState } from '../types/metrics';
import { ThroughputChart } from './ThroughputChart';
import { LatencyChart } from './LatencyChart';
import { TokenGauge } from './TokenGauge';
import { WeightSlider } from './WeightSlider';

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
