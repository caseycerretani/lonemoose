const express = require('express');
const router = express.Router();
const db = require('../db');

// POST /api/reports
router.post('/', (req, res) => {
  const { trail_id, condition, rating, comment } = req.body;

  if (!trail_id || !condition || !rating) {
    return res.status(400).json({ error: 'trail_id, condition, and rating are required' });
  }

  const validConditions = ['Dry', 'Tacky', 'Muddy', 'Snowy', 'Icy', 'Unknown'];
  if (!validConditions.includes(condition)) {
    return res.status(400).json({ error: 'Invalid condition value' });
  }

  const ratingNum = parseInt(rating, 10);
  if (isNaN(ratingNum) || ratingNum < 1 || ratingNum > 5) {
    return res.status(400).json({ error: 'Rating must be between 1 and 5' });
  }

  const trail = db.prepare('SELECT id FROM trails WHERE id = ?').get(trail_id);
  if (!trail) {
    return res.status(404).json({ error: 'Trail not found' });
  }

  try {
    const stmt = db.prepare(`
      INSERT INTO reports (trail_id, condition, rating, comment, created_at)
      VALUES (?, ?, ?, ?, ?)
    `);
    const created_at = new Date().toISOString();
    const result = stmt.run(trail_id, condition, ratingNum, comment || null, created_at);
    const report = db.prepare('SELECT * FROM reports WHERE id = ?').get(result.lastInsertRowid);
    res.status(201).json(report);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
