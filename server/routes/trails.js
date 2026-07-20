const express = require('express');
const router = express.Router();
const db = require('../db');

// GET /api/trails?region=Taos
router.get('/', (req, res) => {
  const { region } = req.query;

  // latest_condition: prefer most-recent rider report, fall back to weather
  let query = `
    SELECT
      t.id,
      t.name,
      t.region,
      t.difficulty,
      t.description,
      t.length_miles,
      t.elevation_gain_ft,
      COUNT(CASE WHEN r.source = 'rider' THEN 1 END) AS report_count,
      ROUND(AVG(CASE WHEN r.source = 'rider' THEN r.rating END), 1) AS avg_rating,
      COALESCE(
        (SELECT r2.condition FROM reports r2
         WHERE r2.trail_id = t.id AND r2.source = 'rider'
         ORDER BY r2.created_at DESC LIMIT 1),
        (SELECT r2.condition FROM reports r2
         WHERE r2.trail_id = t.id AND r2.source = 'weather'
         ORDER BY r2.created_at DESC LIMIT 1)
      ) AS latest_condition,
      COALESCE(
        (SELECT r2.created_at FROM reports r2
         WHERE r2.trail_id = t.id AND r2.source = 'rider'
         ORDER BY r2.created_at DESC LIMIT 1),
        (SELECT r2.created_at FROM reports r2
         WHERE r2.trail_id = t.id AND r2.source = 'weather'
         ORDER BY r2.created_at DESC LIMIT 1)
      ) AS latest_report_at,
      (SELECT r2.source FROM reports r2
       WHERE r2.trail_id = t.id
       ORDER BY r2.created_at DESC LIMIT 1) AS latest_source
    FROM trails t
    LEFT JOIN reports r ON r.trail_id = t.id
  `;

  const params = [];
  if (region && region !== 'All') {
    query += ' WHERE t.region = ?';
    params.push(region);
  }

  query += ' GROUP BY t.id ORDER BY t.name ASC';

  try {
    const trails = db.prepare(query).all(...params);
    res.json(trails);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/trails/:id/reports — rider reports first, then weather
router.get('/:id/reports', (req, res) => {
  try {
    const reports = db.prepare(`
      SELECT id, trail_id, condition, rating, comment, source, created_at
      FROM reports
      WHERE trail_id = ?
      ORDER BY
        CASE source WHEN 'rider' THEN 0 ELSE 1 END,
        created_at DESC
      LIMIT 15
    `).all(req.params.id);
    res.json(reports);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
