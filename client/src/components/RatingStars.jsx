import React from 'react';

export default function RatingStars({ rating, interactive = false, onRate }) {
  const filled = Math.round(rating || 0);

  return (
    <div className="rating-stars" aria-label={`${filled} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((star) => (
        <span
          key={star}
          className={`star${star <= filled ? ' filled' : ''}`}
          onClick={interactive ? () => onRate(star) : undefined}
          style={interactive ? { cursor: 'pointer' } : undefined}
          role={interactive ? 'button' : undefined}
          aria-label={interactive ? `Rate ${star} stars` : undefined}
        >
          ★
        </span>
      ))}
    </div>
  );
}
