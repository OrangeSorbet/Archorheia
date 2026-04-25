interface Props {
  tokens: number;
  max?: number;
  color: string;
}

export function TokenGauge({ tokens, max = 1000, color }: Props) {
  const pct = Math.min(100, Math.max(0, (tokens / max) * 100));

  const getHealthColor = () => {
    if (pct > 60) return color;
    if (pct > 25) return '#f59e0b';
    return '#ef4444';
  };

  return (
    <div className="token-gauge">
      <div className="token-gauge-header">
        <span className="chart-label">Token Bucket</span>
        <span className="token-value" style={{ color: getHealthColor() }}>
          {Math.round(tokens)}
        </span>
      </div>
      <div className="token-track">
        <div
          className="token-fill"
          style={{
            width: `${pct}%`,
            background: getHealthColor(),
            transition: 'width 0.15s ease, background 0.3s ease',
          }}
        />
      </div>
      <div className="token-pct">{pct.toFixed(0)}%</div>
    </div>
  );
}
