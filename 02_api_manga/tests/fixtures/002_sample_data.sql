INSERT INTO manga.kitsu_series_core (
  kitsu_id,
  slug,
  status,
  title_canonical,
  title_en,
  title_ja,
  synopsis_clean,
  rating_average_10,
  rating_rank,
  popularity_rank,
  tags_all_json
) VALUES (
  38,
  'one-piece',
  'current',
  'One Piece',
  'One Piece',
  'ワンピース',
  'Un équipage de pirates cherche un trésor légendaire.',
  8.7,
  12,
  3,
  '["aventure", "pirates", "shounen"]'::jsonb
);

INSERT INTO manga.kitsu_series_authors (kitsu_id, author_name, author_role)
VALUES (38, 'Eiichiro Oda', 'story and art');

INSERT INTO manga.kitsu_weekly_snapshot (
  list_name,
  fetched_at_ts,
  kitsu_id,
  position,
  list_rank,
  trend_rank,
  endpoint
) VALUES
  (
    'trending_weekly', '2026-07-14T12:00:00Z', 38, 1, NULL, 1,
    'https://kitsu.io/api/edge/trending/manga'
  ),
  (
    'most_popular', '2026-07-14T12:00:00Z', 38, 2, 3, NULL,
    'https://kitsu.io/api/edge/manga'
  ),
  (
    'top_publishing', '2026-07-14T12:00:00Z', 38, 4, 12, NULL,
    'https://kitsu.io/api/edge/manga'
  );
