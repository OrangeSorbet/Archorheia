import {
  AreaChart,
  Area,
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

export function ThroughputChart({ history, color }: Props) {
  const data = history.map((p) => ({
    t: p.t,
    v: parseFloat(p.throughput_mbps.toFixed(2)),
  }));

  return (
    <div className="chart-wrap">
      <span className="chart-label">Throughput (Mbps)</span>
      <ResponsiveContainer width="100%" height={90}>
        <AreaChart data={data} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
          <defs>
            <linearGradient id={`tg-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.35} />
              <stop offset="95%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
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
            formatter={(v) => [`${v} Mbps`, 'Throughput']}
            labelFormatter={() => ''}
          />
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#tg-${color.replace('#', '')})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
