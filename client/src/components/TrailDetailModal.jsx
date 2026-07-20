import React, { useState, useEffect } from 'react';
import RatingStars from './RatingStars.jsx';

function timeAgo(isoString) {
  if (!isoString) return null;
  const diff = Date.now() - new Date(isoString).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const DIFFICULTY_LABELS = {
  green: 'Green',
  blue: 'Blue',
  black: 'Black',
  'double-black': 'Double Black',
};

const CONDITION_COLORS = {
  Dry:     { bg: '#b45309', fg: '#fef3c7' },
  Tacky:   { bg: '#15803d', fg: '#dcfce7' },
  Muddy:   { bg: '#92400e', fg: '#fde68a' },
  Snowy:   { bg: '#0369a1', fg: '#e0f2fe' },
  Icy:     { bg: '#0891b2', fg: '#cffafe' },
  Unknown: { bg: '#374151', fg: '#9ca3af' },
};

export default function TrailDetailModal({ trail, onClose, onLogReport }) {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/trails/${trail.id}/reports`)
      .then(r => r.json())
      .then(data => { if (!cancelled) { setReports(data); setLoading(false); } })
      .catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [trail.id]);

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  const condition = trail.latest_condition || 'Unknown';
  const condColor = CONDITION_COLORS[condition] || CONDITION_COLORS.Unknown;

  const riderReports  = reports.filter(r => r.source === 'rider');
  const weatherReport = reports.find(r => r.source === 'weather');

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick} role="dialog" aria-modal="true">
      <div className="modal modal-detail">
        <button className="modal-close" onClick={onClose} aria-label="Close modal">×</button>

        <div className="detail-header">
          <div className="detail-badges">
            <span className={`badge difficulty-badge difficulty-${trail.difficulty}`}>
              {DIFFICULTY_LABELS[trail.difficulty] || trail.difficulty}
            </span>
            <span className="badge condition-badge" style={{ background: condColor.bg, color: condColor.fg }}>
              {condition}
            </span>
            {trail.latest_source === 'weather' && (
              <span className="badge weather-source-badge" title="Condition inferred from Open-Meteo weather data">
                🌤 Weather
              </span>
            )}
            <span className="detail-region">{trail.region}</span>
          </div>
          <h2 className="detail-title">{trail.name}</h2>
          <p className="detail-desc">{trail.description}</p>

          <div className="detail-stats">
            <div className="detail-stat">
              <span className="detail-stat-value">{trail.length_miles}</span>
              <span className="detail-stat-label">miles</span>
            </div>
            <div className="detail-stat-divider" />
            <div className="detail-stat">
              <span className="detail-stat-value">{trail.elevation_gain_ft?.toLocaleString()}</span>
              <span className="detail-stat-label">ft gain</span>
            </div>
            {trail.avg_rating && (
              <>
                <div className="detail-stat-divider" />
                <div className="detail-stat">
                  <RatingStars rating={trail.avg_rating} />
                  <span className="detail-stat-label">{trail.avg_rating} avg from riders</span>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="reports-section">
          <div className="reports-header">
            <h3 className="reports-title">Rider Reports</h3>
            <button className="btn btn-primary btn-sm" onClick={onLogReport}>
              + Log Report
            </button>
          </div>

          {loading ? (
            <div className="reports-loading">
              {[1,2,3].map(i => <div key={i} className="report-skeleton" />)}
            </div>
          ) : riderReports.length === 0 ? (
            <div className="reports-empty">
              <p>No rider reports yet.</p>
              <p>Be the first to log conditions on this trail.</p>
            </div>
          ) : (
            <ul className="reports-list">
              {riderReports.map(r => {
                const rc = CONDITION_COLORS[r.condition] || CONDITION_COLORS.Unknown;
                return (
                  <li key={r.id} className="report-item">
                    <div className="report-top">
                      <span className="badge condition-badge" style={{ background: rc.bg, color: rc.fg }}>
                        {r.condition}
                      </span>
                      <RatingStars rating={r.rating} />
                      <span className="report-time">{timeAgo(r.created_at)}</span>
                    </div>
                    {r.comment && <p className="report-comment">{r.comment}</p>}
                  </li>
                );
              })}
            </ul>
          )}

          {weatherReport && (
            <div className="weather-report-block">
              <div className="weather-report-header">
                <span className="weather-report-label">🌤 Weather Estimate</span>
                <span className="report-time">{timeAgo(weatherReport.created_at)}</span>
              </div>
              <div className="weather-report-body">
                {(() => {
                  const rc = CONDITION_COLORS[weatherReport.condition] || CONDITION_COLORS.Unknown;
                  return (
                    <span className="badge condition-badge" style={{ background: rc.bg, color: rc.fg }}>
                      {weatherReport.condition}
                    </span>
                  );
                })()}
                <p className="weather-report-comment">{weatherReport.comment}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
