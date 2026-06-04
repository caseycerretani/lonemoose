import React from 'react';

const REGIONS = ['All', 'Taos', 'Angel Fire'];

export default function RegionFilter({ selected, onChange }) {
  return (
    <div className="region-filter">
      {REGIONS.map((region) => (
        <button
          key={region}
          className={`region-pill${selected === region ? ' active' : ''}`}
          onClick={() => onChange(region)}
        >
          {region}
        </button>
      ))}
    </div>
  );
}
