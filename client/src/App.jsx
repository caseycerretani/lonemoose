import React, { useState, useEffect, useCallback, useRef } from 'react';
import TrailCard from './components/TrailCard.jsx';
import TrailDetailModal from './components/TrailDetailModal.jsx';
import ReportModal from './components/ReportModal.jsx';
import RegionFilter from './components/RegionFilter.jsx';

const TRAIL_REFRESH_INTERVAL  = 12 * 60 * 60 * 1000; // 12 hours
const WEATHER_STATUS_INTERVAL =  6 * 60 * 60 * 1000; // 6 hours

function timeAgo(isoString) {
  if (!isoString) return null;
  const diff = Date.now() - new Date(isoString).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function App() {
  const [trails, setTrails]               = useState([]);
  const [loading, setLoading]             = useState(true);
  const [selectedRegion, setSelectedRegion] = useState('All');
  const [detailTrail, setDetailTrail]     = useState(null);
  const [reportTrail, setReportTrail]     = useState(null);
  const [weatherStatus, setWeatherStatus] = useState(null);
  const intervalRef = useRef(null);

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

  const fetchWeatherStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/weather/status');
      const data = await res.json();
      setWeatherStatus(data);
    } catch (_) {}
  }, []);

  useEffect(() => {
    fetchTrails();
    intervalRef.current = setInterval(fetchTrails, TRAIL_REFRESH_INTERVAL);
    return () => clearInterval(intervalRef.current);
  }, [fetchTrails]);

  useEffect(() => {
    fetchWeatherStatus();
    const id = setInterval(fetchWeatherStatus, WEATHER_STATUS_INTERVAL);
    return () => clearInterval(id);
  }, [fetchWeatherStatus]);

  const handleReportSubmit = async () => {
    setReportTrail(null);
    setDetailTrail(null);
    await fetchTrails();
  };

  const openDetail = (trail) => { setDetailTrail(trail); setReportTrail(null); };
  const openReport = (trail) => { setReportTrail(trail); setDetailTrail(null); };

  const weatherUpdatedAt = weatherStatus?.lastUpdate
    ? timeAgo(weatherStatus.lastUpdate)
    : null;

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
          {weatherUpdatedAt && (
            <div className="weather-status" title="Condition data auto-updated from Open-Meteo">
              🌤 Weather updated {weatherUpdatedAt}
            </div>
          )}
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
          <div className="empty-state"><p>No trails found for this region.</p></div>
        ) : (
          <div className="trail-grid">
            {trails.map((trail) => (
              <TrailCard
                key={trail.id}
                trail={trail}
                onClick={() => openDetail(trail)}
              />
            ))}
          </div>
        )}
      </main>

      {detailTrail && (
        <TrailDetailModal
          trail={detailTrail}
          onClose={() => setDetailTrail(null)}
          onLogReport={() => openReport(detailTrail)}
        />
      )}

      {reportTrail && (
        <ReportModal
          trail={reportTrail}
          onClose={() => setReportTrail(null)}
          onSubmit={handleReportSubmit}
        />
      )}
    </div>
  );
}
