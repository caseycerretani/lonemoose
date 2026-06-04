const express = require('express');
const router = express.Router();
const db = require('../db');

// GET /api/trails?region=Taos
router.get('/', (req, res) => {
  const { region } = req.query;
  let query = `
    SELECT
      t.id,
      t.name,
      t.region,
      t.difficulty,
      t.description,
      t.length_miles,
      t.elevation_gain_ft,
      COUNT(r.id) AS report_count,
      ROUND(AVG(r.rating), 1) AS avg_rating,
      (
        SELECT r2.condition
        FROM reports r2
        WHERE r2.trail_id = t.id
        ORDER BY r2.created_at DESC
        LIMIT 1
      ) AS latest_condition,
      (
        SELECT r2.created_at
        FROM reports r2
        WHERE r2.trail_id = t.id
        ORDER BY r2.created_at DESC
        LIMIT 1
      ) AS latest_report_at
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

// GET /api/trails/:id/reports
router.get('/:id/reports', (req, res) => {
  const { id } = req.params;
  try {
    const reports = db.prepare(`
      SELECT id, trail_id, condition, rating, comment, created_at
      FROM reports
      WHERE trail_id = ?
      ORDER BY created_at DESC
      LIMIT 10
    `).all(id);
    res.json(reports);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
