import { useEffect, useState } from 'react';
import { useMetrics } from './hooks/useMetrics';
import { api } from './api/http';
import { GlobalControls } from './components/GlobalControls';
import { AppLane } from './components/AppLane';
import { AddAppModal } from './components/AddAppModal';
import type { AppMode } from './types/metrics';
import './App.css';

// Ordered palette — each app gets one color, wraps around
const PALETTE = [
  '#38bdf8', // sky
  '#a78bfa', // violet
  '#34d399', // emerald
  '#fb923c', // orange
  '#f472b6', // pink
  '#facc15', // amber
  '#22d3ee', // cyan
  '#f87171', // red
];

function colorFor(name: string, index: number) {
  // Stable color by name hash so it doesn't shift when other apps are removed
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length] ?? PALETTE[index % PALETTE.length];
}

export default function App() {
  const { apps, connected } = useMetrics();
  const [mode, setMode] = useState<AppMode>('sim');
  const [bandwidth, setBandwidth] = useState(100);
  const [showModal, setShowModal] = useState(false);
  // Track manually removed apps (until WS stops reporting them)
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // Fetch initial status
  useEffect(() => {
    api.getStatus().then((s) => {
      setMode(s.mode);
      // Backend may return bandwidth_mbps or bandwidth
      const bw = s.bandwidth_mbps ?? s.bandwidth ?? 100;
      setBandwidth(bw);
    }).catch(() => {/* backend not up yet — defaults are fine */});
  }, []);

  const visibleApps = Object.entries(apps).filter(([name]) => !hidden.has(name));

  const handleRemove = (name: string) => {
    setHidden((prev) => new Set(prev).add(name));
  };

  return (
    <div className="app-root">
      <GlobalControls
        mode={mode}
        bandwidth={bandwidth}
        connected={connected}
        onModeChange={setMode}
        onBandwidthChange={setBandwidth}
        onAddApp={() => setShowModal(true)}
      />

      <main className="app-main">
        {visibleApps.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">⬡</div>
            <h2 className="empty-title">No apps tracked</h2>
            <p className="empty-sub">
              {connected
                ? 'Click "+ Add App" to start shaping traffic.'
                : 'Connecting to backend…'}
            </p>
            {connected && (
              <button className="btn-primary" onClick={() => setShowModal(true)}>
                + Add App
              </button>
            )}
          </div>
        ) : (
          <div className="lanes-grid">
            {visibleApps.map(([name, state], idx) => (
              <AppLane
                key={name}
                appState={state}
                color={colorFor(name, idx)}
                onRemove={handleRemove}
              />
            ))}
          </div>
        )}
      </main>

      {showModal && (
        <AddAppModal
          mode={mode}
          onClose={() => setShowModal(false)}
          onAdded={(name: string) => {
            // If a previously-hidden app is re-added, unhide it
            setHidden((prev) => {
              const next = new Set(prev);
              next.delete(name);
              return next;
            });
            setShowModal(false);
          }}
        />
      )}
    </div>
  );
}
