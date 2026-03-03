-- ============================================================
-- Pinewood Derby Race Day App — Supabase Schema
-- Run this entire file in the Supabase SQL Editor
-- ============================================================

-- ── Cars ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cars (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  car_number    INT         UNIQUE,
  kid_name      TEXT        NOT NULL,
  image_url     TEXT,
  -- device_token ties all cars registered from the same device/family together.
  -- One QR code per device shows ALL cars for that family at inspection.
  device_token  TEXT        NOT NULL DEFAULT gen_random_uuid()::TEXT,
  legal_status  TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (legal_status IN ('pending','legal','not_legal')),
  eliminated    BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-assign car_number (1, 2, 3 …) on every insert
CREATE OR REPLACE FUNCTION assign_car_number()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.car_number := (SELECT COALESCE(MAX(car_number), 0) + 1 FROM cars);
  RETURN NEW;
END;
$$;

-- Migrate existing tables: swap qr_token → device_token if needed
ALTER TABLE cars ADD COLUMN IF NOT EXISTS device_token TEXT NOT NULL DEFAULT gen_random_uuid()::TEXT;
ALTER TABLE cars DROP COLUMN IF EXISTS qr_token;
-- Parent email for registration confirmation
ALTER TABLE cars ADD COLUMN IF NOT EXISTS email TEXT;

DROP TRIGGER IF EXISTS trg_car_number ON cars;
CREATE TRIGGER trg_car_number
  BEFORE INSERT ON cars
  FOR EACH ROW EXECUTE FUNCTION assign_car_number();


-- ── Rounds ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rounds (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  round_number  INT         UNIQUE NOT NULL,
  status        TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','active','completed')),
  advance_count INT,        -- set by host before closing round
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Heats ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS heats (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  round_id     UUID NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  heat_number  INT  NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','active','completed')),
  UNIQUE(round_id, heat_number)
);


-- ── Heat entries (lane assignments — locked when round starts) ─
CREATE TABLE IF NOT EXISTS heat_entries (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  heat_id     UUID NOT NULL REFERENCES heats(id) ON DELETE CASCADE,
  lane_number INT  NOT NULL CHECK (lane_number BETWEEN 1 AND 4),
  car_id      UUID NOT NULL REFERENCES cars(id),
  UNIQUE(heat_id, lane_number),
  UNIQUE(heat_id, car_id)
);


-- ── Heat results ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS heat_results (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  heat_id         UUID NOT NULL REFERENCES heats(id) ON DELETE CASCADE UNIQUE,
  first_place_car UUID NOT NULL REFERENCES cars(id),
  second_place_car UUID NOT NULL REFERENCES cars(id),
  third_place_car  UUID NOT NULL REFERENCES cars(id),
  fourth_place_car UUID NOT NULL REFERENCES cars(id),
  entered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── App state singleton (tracks current round / heat) ─────────
CREATE TABLE IF NOT EXISTS race_state (
  id               INT  PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  current_round_id UUID REFERENCES rounds(id),
  current_heat_id  UUID REFERENCES heats(id),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO race_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Email toggle — enables/disables sending confirmation emails on registration
ALTER TABLE race_state ADD COLUMN IF NOT EXISTS email_enabled BOOLEAN NOT NULL DEFAULT false;


-- ── Current-round standings view ──────────────────────────────
-- Points = sum of finish positions in current round (lower = better)
CREATE OR REPLACE VIEW round_standings AS
WITH current_round AS (
  SELECT rs.current_round_id AS round_id
  FROM   race_state rs
  WHERE  rs.id = 1
),
car_points AS (
  SELECT
    c.id              AS car_id,
    c.car_number,
    c.kid_name,
    -- 1pt for 1st, 2pt for 2nd, 3pt for 3rd, 4pt for 4th
    SUM(
      CASE
        WHEN hr.first_place_car  = c.id THEN 1
        WHEN hr.second_place_car = c.id THEN 2
        WHEN hr.third_place_car  = c.id THEN 3
        WHEN hr.fourth_place_car = c.id THEN 4
        ELSE 0
      END
    )                 AS total_points,
    COUNT(hr.id)      AS heats_completed
  FROM   cars c
  JOIN   current_round cr ON TRUE
  JOIN   heats h  ON h.round_id = cr.round_id
  LEFT JOIN heat_results hr ON hr.heat_id = h.id
    AND (hr.first_place_car  = c.id
      OR hr.second_place_car = c.id
      OR hr.third_place_car  = c.id
      OR hr.fourth_place_car = c.id)
  WHERE  c.eliminated = FALSE
  GROUP BY c.id, c.car_number, c.kid_name
)
SELECT
  car_id,
  car_number,
  kid_name,
  total_points,
  heats_completed,
  RANK() OVER (ORDER BY total_points ASC, heats_completed DESC) AS standing
FROM car_points;


-- ── RLS — open policies (anon key, no auth) ───────────────────
ALTER TABLE cars         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rounds       ENABLE ROW LEVEL SECURITY;
ALTER TABLE heats        ENABLE ROW LEVEL SECURITY;
ALTER TABLE heat_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE heat_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_state   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_all" ON cars;
DROP POLICY IF EXISTS "anon_all" ON rounds;
DROP POLICY IF EXISTS "anon_all" ON heats;
DROP POLICY IF EXISTS "anon_all" ON heat_entries;
DROP POLICY IF EXISTS "anon_all" ON heat_results;
DROP POLICY IF EXISTS "anon_all" ON race_state;

CREATE POLICY "anon_all" ON cars         FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all" ON rounds       FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all" ON heats        FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all" ON heat_entries FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all" ON heat_results FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all" ON race_state   FOR ALL TO anon USING (true) WITH CHECK (true);


-- ── Storage (run AFTER creating the bucket in the dashboard) ──
-- These policies allow anon to upload and view car images.
-- Create a bucket called "car-images" (public) first, then run:

INSERT INTO storage.buckets (id, name, public)
VALUES ('car-images', 'car-images', true)
ON CONFLICT (id) DO NOTHING;

DROP POLICY IF EXISTS "anon_upload" ON storage.objects;
DROP POLICY IF EXISTS "anon_read"   ON storage.objects;

CREATE POLICY "anon_upload" ON storage.objects
  FOR INSERT TO anon
  WITH CHECK (bucket_id = 'car-images');

CREATE POLICY "anon_read" ON storage.objects
  FOR SELECT TO anon
  USING (bucket_id = 'car-images');
