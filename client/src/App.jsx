import React, { useState, useEffect, useCallback } from 'react';
import TrailCard from './components/TrailCard.jsx';
import ReportModal from './components/ReportModal.jsx';
import RegionFilter from './components/RegionFilter.jsx';

export default function App() {
  const [trails, setTrails] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRegion, setSelectedRegion] = useState('All');
  const [selectedTrail, setSelectedTrail] = useState(null);

  const fetchTrails = useCallback(async () => {
    setLoading(true);
    try {
      const url = selectedRegion === 'All'
        ? '/api/trails'
        : `/api/trails?region=${encodeURIComponent(selectedRegion)}`;
      const res = await fetch(url);
      const data = await res.json();
      setTrails(data);
    } catch (err) {
      console.error('Failed to fetch trails:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedRegion]);

  useEffect(() => {
    fetchTrails();
  }, [fetchTrails]);

  const handleReportSubmit = async () => {
    setSelectedTrail(null);
    await fetchTrails();
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon">🦌</span>
            <div>
              <h1>Lonemoose</h1>
              <p>Trail Conditions — Taos &amp; Angel Fire</p>
            </div>
          </div>
        </div>
      </header>

      <main className="main-content">
        <RegionFilter selected={selectedRegion} onChange={setSelectedRegion} />

        {loading ? (
          <div className="trail-grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="trail-card skeleton">
                <div className="skeleton-line skeleton-title" />
                <div className="skeleton-line skeleton-badge" />
                <div className="skeleton-line skeleton-text" />
                <div className="skeleton-line skeleton-text short" />
              </div>
            ))}
          </div>
        ) : trails.length === 0 ? (
          <div className="empty-state">
            <p>No trails found for this region.</p>
          </div>
        ) : (
          <div className="trail-grid">
            {trails.map((trail) => (
              <TrailCard
                key={trail.id}
                trail={trail}
                onClick={() => setSelectedTrail(trail)}
              />
            ))}
          </div>
        )}
      </main>

      {selectedTrail && (
        <ReportModal
          trail={selectedTrail}
          onClose={() => setSelectedTrail(null)}
          onSubmit={handleReportSubmit}
        />
      )}
    </div>
  );
}
