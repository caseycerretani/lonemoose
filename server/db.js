const Database = require('better-sqlite3');
const path = require('path');

const db = new Database(path.join(__dirname, 'trails.db'));

db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
  CREATE TABLE IF NOT EXISTS trails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    description TEXT,
    length_miles REAL,
    elevation_gain_ft INTEGER
  );

  CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trail_id INTEGER NOT NULL,
    condition TEXT NOT NULL,
    rating INTEGER CHECK(rating BETWEEN 1 AND 5),
    comment TEXT,
    source TEXT NOT NULL DEFAULT 'rider',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (trail_id) REFERENCES trails(id)
  );
`);

// Migrate existing DBs: add source column if missing, drop NOT NULL on rating
try {
  db.exec(`ALTER TABLE reports ADD COLUMN source TEXT NOT NULL DEFAULT 'rider'`);
} catch (_) { /* column already exists */ }
// SQLite can't drop constraints, but the new CREATE TABLE above handles fresh DBs correctly.

const trailCount = db.prepare('SELECT COUNT(*) as count FROM trails').get();
if (trailCount.count === 0) {
  const insertTrail = db.prepare(`
    INSERT INTO trails (name, region, difficulty, description, length_miles, elevation_gain_ft)
    VALUES (?, ?, ?, ?, ?, ?)
  `);

  const trails = [
    ['South Boundary Trail', 'Taos', 'blue', 'Epic singletrack traversing the mountains south of Taos', 20.2, 2100],
    ['Devisadero Loop', 'Taos', 'green', 'Mellow loop with great Taos valley views', 4.5, 650],
    ['Italianos Trail', 'Taos', 'black', 'Technical descent through aspen groves', 8.1, 1800],
    ['West Rim Trail', 'Taos', 'blue', 'Rim trail with sweeping Rio Grande gorge views', 9.4, 1200],
    ["Marta's Loop", 'Taos', 'green', 'Beginner-friendly loop near downtown Taos', 3.2, 400],
    ['Upper Hondo', 'Taos', 'double-black', 'Steep technical terrain, experts only', 5.6, 2400],
    ['El Oso', 'Angel Fire', 'blue', 'Flowy intermediate trail through ponderosa pines', 6.3, 900],
    ['Bobcat', 'Angel Fire', 'black', 'Fast and technical with rock features', 4.8, 1100],
    ['Enchanted Forest', 'Angel Fire', 'green', 'Beginner-friendly cross-country loop', 3.7, 500],
    ['Purgatory', 'Angel Fire', 'black', 'Sustained steep descent with natural rock slabs', 5.2, 1300],
    ['Outlaw', 'Angel Fire', 'double-black', 'Gnarliest descent in Angel Fire', 3.1, 1500],
    ['Greens Gap', 'Angel Fire', 'blue', 'Rolling terrain connecting upper bike park zones', 7.8, 950],
  ];

  const insertMany = db.transaction((trails) => {
    for (const t of trails) insertTrail.run(...t);
  });
  insertMany(trails);

  const insertReport = db.prepare(`
    INSERT INTO reports (trail_id, condition, rating, comment, created_at)
    VALUES (?, ?, ?, ?, ?)
  `);

  const now = Date.now();
  const hoursAgo = (h) => new Date(now - h * 3600 * 1000).toISOString();

  const reports = [
    [1, 'Tacky', 5, 'Perfect hero dirt, rode it yesterday morning', hoursAgo(18)],
    [1, 'Dry', 4, 'A bit dusty in spots but still great', hoursAgo(72)],
    [2, 'Dry', 3, 'Flowy but could use some moisture', hoursAgo(10)],
    [3, 'Muddy', 2, 'Lower section is a mess after the storm', hoursAgo(5)],
    [3, 'Tacky', 5, 'Incredible conditions before the rain', hoursAgo(120)],
    [4, 'Dry', 4, 'Rim views are stunning, trail is in solid shape', hoursAgo(36)],
    [5, 'Tacky', 5, 'Great beginner trail, kids loved it', hoursAgo(48)],
    [6, 'Snowy', 2, 'Snow patches on north-facing sections, approach with caution', hoursAgo(8)],
    [7, 'Tacky', 5, 'Best flow trail in the area, ripping fast', hoursAgo(14)],
    [7, 'Dry', 4, 'Slightly loose on the berms but still super fun', hoursAgo(96)],
    [8, 'Dry', 4, 'Rock features are dry and grippy', hoursAgo(22)],
    [9, 'Tacky', 5, 'Perfect for a beginner shred session', hoursAgo(30)],
    [10, 'Muddy', 3, 'Some wet sections mid-trail, bring fenders', hoursAgo(6)],
    [11, 'Dry', 5, 'Absolutely gnarly, not for the faint of heart', hoursAgo(44)],
    [12, 'Tacky', 4, 'Rolling terrain is in great shape, caught it at golden hour', hoursAgo(58)],
  ];

  const insertReports = db.transaction((reports) => {
    for (const r of reports) insertReport.run(...r);
  });
  insertReports(reports);
}

module.exports = db;
