import React, { useState } from 'react';
import RatingStars from './RatingStars.jsx';

const CONDITIONS = ['Dry', 'Tacky', 'Muddy', 'Snowy', 'Icy', 'Unknown'];

export default function ReportModal({ trail, onClose, onSubmit }) {
  const [condition, setCondition] = useState('');
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!condition) {
      setError('Please select a condition.');
      return;
    }
    if (!rating) {
      setError('Please select a star rating.');
      return;
    }
    setError('');
    setSubmitting(true);
    try {
      const res = await fetch('/api/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trail_id: trail.id, condition, rating, comment: comment.trim() || undefined }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Failed to submit report');
      }
      await onSubmit();
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick} role="dialog" aria-modal="true">
      <div className="modal">
        <button className="modal-close" onClick={onClose} aria-label="Close modal">×</button>

        <div className="modal-header">
          <h2 className="modal-title">Log Condition Report</h2>
          <p className="modal-subtitle">{trail.name} · {trail.region}</p>
        </div>

        <form onSubmit={handleSubmit} className="report-form">
          <fieldset className="form-group">
            <legend className="form-label">Trail Condition</legend>
            <div className="condition-options">
              {CONDITIONS.map((c) => (
                <label key={c} className={`condition-option condition-${c.toLowerCase()}${condition === c ? ' selected' : ''}`}>
                  <input
                    type="radio"
                    name="condition"
                    value={c}
                    checked={condition === c}
                    onChange={() => setCondition(c)}
                  />
                  {c}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="form-group">
            <label className="form-label">Your Rating</label>
            <RatingStars rating={rating} interactive onRate={setRating} />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="comment">Comment <span className="optional">(optional)</span></label>
            <textarea
              id="comment"
              className="form-textarea"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Describe trail conditions, hazards, recent changes..."
              rows={3}
              maxLength={500}
            />
          </div>

          {error && <p className="form-error">{error}</p>}

          <div className="form-actions">
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Submitting…' : 'Submit Report'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
