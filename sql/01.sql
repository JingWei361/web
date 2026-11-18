-- ============================================
-- pgvector integration for AI Travel Assistant
-- Run this in Supabase SQL Editor after 00.sql
-- ============================================

create extension if not exists vector;

-- Shared spot knowledge base (grows from AI recognition results)
create table if not exists public.spot_embeddings (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  emoji text,
  category text,
  description text,
  address text,
  city text,
  source text not null default 'ai',
  embedding vector(768) not null,
  metadata jsonb,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

-- Avoid duplicate spots for the same city (NULL city treated as empty string)
create unique index if not exists idx_spot_embeddings_name_city
on public.spot_embeddings (name, coalesce(city, ''));

create index if not exists idx_spot_embeddings_city
on public.spot_embeddings (city);

create index if not exists idx_spot_embeddings_category
on public.spot_embeddings (category);

-- IVFFlat index for cosine distance search
-- (safe to create even when the table is still empty)
create index if not exists idx_spot_embeddings_embedding
on public.spot_embeddings
using ivfflat (embedding vector_cosine_ops)
with (lists = 100);

drop trigger if exists set_spot_embeddings_updated_at on public.spot_embeddings;
create trigger set_spot_embeddings_updated_at
before update on public.spot_embeddings
for each row execute function public.handle_updated_at();

-- Similarity search RPC used by the Flask backend
create or replace function public.match_spot_embeddings(
  query_embedding vector(768),
  match_count int default 5,
  filter_city text default null,
  min_similarity float default 0.0
)
returns table (
  id uuid,
  name text,
  emoji text,
  description text,
  address text,
  city text,
  similarity float
)
language sql
stable
as $$
  select
    s.id,
    s.name,
    s.emoji,
    s.description,
    s.address,
    s.city,
    (1 - (s.embedding <=> query_embedding))::float as similarity
  from public.spot_embeddings s
  where
    (filter_city is null or s.city = filter_city)
    and (1 - (s.embedding <=> query_embedding)) >= min_similarity
  order by s.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;

-- Optional: store each recognition query for analytics / personalization later
create table if not exists public.recognition_embeddings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete set null,
  landmark_name text not null,
  description text,
  embedding vector(768) not null,
  recommendation_data jsonb,
  created_at timestamptz not null default timezone('utc'::text, now())
);

create index if not exists idx_recognition_embeddings_user_id
on public.recognition_embeddings (user_id);

create index if not exists idx_recognition_embeddings_embedding
on public.recognition_embeddings
using ivfflat (embedding vector_cosine_ops)
with (lists = 100);

-- RLS
alter table public.spot_embeddings enable row level security;
alter table public.recognition_embeddings enable row level security;

-- Shared knowledge base: readable by everyone (including anon)
drop policy if exists "Anyone can read spot embeddings" on public.spot_embeddings;
create policy "Anyone can read spot embeddings"
on public.spot_embeddings
for select
using (true);

-- Writes typically go through the service role key from the backend.
-- If you prefer authenticated users to contribute, uncomment below:
-- drop policy if exists "Authenticated users can insert spot embeddings" on public.spot_embeddings;
-- create policy "Authenticated users can insert spot embeddings"
-- on public.spot_embeddings
-- for insert
-- to authenticated
-- with check (true);

drop policy if exists "Users can read own recognition embeddings" on public.recognition_embeddings;
create policy "Users can read own recognition embeddings"
on public.recognition_embeddings
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own recognition embeddings" on public.recognition_embeddings;
create policy "Users can insert own recognition embeddings"
on public.recognition_embeddings
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own recognition embeddings" on public.recognition_embeddings;
create policy "Users can delete own recognition embeddings"
on public.recognition_embeddings
for delete
using (auth.uid() = user_id);

grant execute on function public.match_spot_embeddings(vector, int, text, float) to anon, authenticated, service_role;
