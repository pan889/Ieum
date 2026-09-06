-- 최초 기동 시 1회 실행된다. 확장 설치는 마이그레이션이 아니라 여기서 한다
-- (마이그레이션은 슈퍼유저 권한을 가정하지 않는다).
CREATE EXTENSION IF NOT EXISTS citext;      -- user.email 대소문자 무시 unique
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 부분일치 보조 인덱스
CREATE EXTENSION IF NOT EXISTS ltree;       -- wiki page.path (M2)
CREATE EXTENSION IF NOT EXISTS pgroonga;    -- 한국어 전문검색 (M2)
