import { useEffect, useRef, useState } from 'react';
import { api } from '../api/http';

interface Props {
  name: string;
  weight: number;
  color: string;
}

export function WeightSlider({ name, weight, color }: Props) {
  const [local, setLocal] = useState(weight);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync if backend weight changes externally
  useEffect(() => {
    setLocal(weight);
  }, [weight]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseInt(e.target.value, 10);
    setLocal(v);

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setSaving(true);
      try {
        await api.setWeight(name, v);
      } catch {
        // silently ignore — next tick will resync from WS
      } finally {
        setSaving(false);
      }
    }, 300);
  };

  return (
    <div className="weight-slider">
      <div className="weight-header">
        <span className="chart-label">WFQ Priority</span>
        <span className="weight-value" style={{ color }}>
          {local}
          {saving && <span className="weight-saving"> …</span>}
        </span>
      </div>
      <input
        type="range"
        min={1}
        max={10}
        step={1}
        value={local}
        onChange={handleChange}
        className="slider"
        style={{ '--thumb-color': color } as React.CSSProperties}
      />
      <div className="weight-ticks">
        {Array.from({ length: 10 }, (_, i) => (
          <span key={i} className={i + 1 === local ? 'tick active' : 'tick'}>
            {i + 1}
          </span>
        ))}
      </div>
    </div>
  );
}
