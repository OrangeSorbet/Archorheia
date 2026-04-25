import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { MetricsPoint } from '../types/metrics';

interface Props {
  history: MetricsPoint[];
  color: string;
}

export function LatencyChart({ history, color }: Props) {
  const data = history.map((p) => ({
    t: p.t,
    v: parseFloat(p.latency_ms.toFixed(1)),
  }));

  return (
    <div className="chart-wrap">
      <span className="chart-label">Latency (ms)</span>
      <ResponsiveContainer width="100%" height={90}>
        <LineChart data={data} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
          <XAxis dataKey="t" hide />
          <YAxis
            tick={{ fill: 'var(--c-muted)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={36}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--c-surface)',
              border: '1px solid var(--c-border)',
              borderRadius: 6,
              fontSize: 12,
              color: 'var(--c-text)',
            }}
            formatter={(v) => [`${v} ms`, 'Latency']}
            labelFormatter={() => ''}
          />
          <Line
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
