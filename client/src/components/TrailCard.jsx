import React from 'react';
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

const CONDITION_LABELS = {
  Dry: 'Dry',
  Tacky: 'Tacky',
  Muddy: 'Muddy',
  Snowy: 'Snowy',
  Icy: 'Icy',
  Unknown: 'Unknown',
};

export default function TrailCard({ trail, onClick }) {
  const {
    name,
    region,
    difficulty,
    description,
    length_miles,
    elevation_gain_ft,
    avg_rating,
    report_count,
    latest_condition,
    latest_report_at,
  } = trail;

  const condition = latest_condition || 'Unknown';
  const updatedAt = timeAgo(latest_report_at);

  return (
    <article className="trail-card" onClick={onClick} role="button" tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}>
      <div className="card-header">
        <div className="card-badges">
          <span className={`badge difficulty-badge difficulty-${difficulty}`}>
            {DIFFICULTY_LABELS[difficulty] || difficulty}
          </span>
          <span className={`badge condition-badge condition-${condition.toLowerCase()}`}>
            {CONDITION_LABELS[condition] || condition}
          </span>
        </div>
        <span className="region-label">{region}</span>
      </div>

      <h2 className="trail-name">{name}</h2>
      <p className="trail-description">{description}</p>

      <div className="trail-stats">
        <span className="stat">
          <span className="stat-value">{length_miles}</span>
          <span className="stat-label">mi</span>
        </span>
        <span className="stat-divider" />
        <span className="stat">
          <span className="stat-value">{elevation_gain_ft?.toLocaleString()}</span>
          <span className="stat-label">ft gain</span>
        </span>
      </div>

      <div className="card-footer">
        <div className="rating-row">
          <RatingStars rating={avg_rating} />
          <span className="report-count">
            {report_count > 0 ? `${report_count} report${report_count !== 1 ? 's' : ''}` : 'No reports yet'}
          </span>
        </div>
        {updatedAt && (
          <span className="last-updated">Updated {updatedAt}</span>
        )}
      </div>

      <div className="card-cta">Log a condition report →</div>
    </article>
  );
}
