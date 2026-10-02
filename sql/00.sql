-- Supabase schema for a Flask web app
-- Frontend: JavaScript + Bootstrap 5
-- Backend: Python + SQL
-- Authentication is handled by Supabase Auth.
-- Do not store passwords in public tables.

create extension if not exists pgcrypto;

-- 會員資料：對應 Supabase auth.users
create table if not exists public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  full_name text not null,
  phone text not null,
  email text not null unique,
  gender text not null check (gender in ('male', 'female', 'other')),
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now())
);

-- AI 隨機推薦內容
create table if not exists public.ai_recommendations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  recommendation_text text not null,
  recommendation_data jsonb,
  source text,
  created_at timestamptz not null default timezone('utc'::text, now())
);

-- 收藏 AI 隨機推薦
create table if not exists public.ai_recommendation_favorites (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  recommendation_id uuid not null references public.ai_recommendations (id) on delete cascade,
  created_at timestamptz not null default timezone('utc'::text, now()),
  unique (user_id, recommendation_id)
);

-- AI 生成紀錄
create table if not exists public.ai_generation_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  record_type text not null default 'itinerary' check (record_type in ('itinerary', 'recommendation')),
  title text,
  prompt text not null,
  generated_content text not null,
  context_data jsonb,
  model_name text,
  request_payload jsonb,
  response_payload jsonb,
  created_at timestamptz not null default timezone('utc'::text, now())
);

-- 收藏 AI 行程規劃與生成紀錄
create table if not exists public.ai_generation_favorites (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  generation_record_id uuid not null references public.ai_generation_records (id) on delete cascade,
  created_at timestamptz not null default timezone('utc'::text, now()),
  unique (user_id, generation_record_id)
);

-- 瀏覽與搜尋紀錄
create table if not exists public.search_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  search_query text not null,
  search_filters jsonb,
  created_at timestamptz not null default timezone('utc'::text, now())
);

create index if not exists idx_profiles_email on public.profiles (email);
create index if not exists idx_ai_recommendations_user_id on public.ai_recommendations (user_id);
create index if not exists idx_ai_recommendation_favorites_user_id on public.ai_recommendation_favorites (user_id);
create index if not exists idx_ai_generation_records_user_id on public.ai_generation_records (user_id);
create index if not exists idx_ai_generation_favorites_user_id on public.ai_generation_favorites (user_id);
create index if not exists idx_search_history_user_id on public.search_history (user_id);

-- 自動同步 Supabase Auth 使用者資料到 profiles
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_full_name text;
  v_phone text;
  v_gender text;
begin
  v_full_name := nullif(trim(coalesce(new.raw_user_meta_data->>'full_name', '')), '');
  v_phone := nullif(trim(coalesce(new.raw_user_meta_data->>'phone', '')), '');
  v_gender := nullif(trim(coalesce(new.raw_user_meta_data->>'gender', '')), '');

  if v_full_name is null then
    raise exception 'full_name is required';
  end if;

  if v_phone is null then
    raise exception 'phone is required';
  end if;

  if v_gender is null then
    raise exception 'gender is required';
  end if;

  insert into public.profiles (user_id, full_name, phone, email, gender)
  values (
    new.id,
    v_full_name,
    v_phone,
    new.email,
    v_gender
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

create or replace function public.handle_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$;

drop trigger if exists set_profiles_updated_at on public.profiles;
create trigger set_profiles_updated_at
before update on public.profiles
for each row execute function public.handle_updated_at();

alter table public.profiles enable row level security;
alter table public.ai_recommendations enable row level security;
alter table public.ai_recommendation_favorites enable row level security;
alter table public.ai_generation_records enable row level security;
alter table public.ai_generation_favorites enable row level security;
alter table public.search_history enable row level security;

-- profiles policies
drop policy if exists "Users can read own profile" on public.profiles;
create policy "Users can read own profile"
on public.profiles
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own profile" on public.profiles;
create policy "Users can insert own profile"
on public.profiles
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can update own profile" on public.profiles;
create policy "Users can update own profile"
on public.profiles
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

-- ai_recommendations policies
drop policy if exists "Users can read own recommendations" on public.ai_recommendations;
create policy "Users can read own recommendations"
on public.ai_recommendations
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own recommendations" on public.ai_recommendations;
create policy "Users can insert own recommendations"
on public.ai_recommendations
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own recommendations" on public.ai_recommendations;
create policy "Users can delete own recommendations"
on public.ai_recommendations
for delete
using (auth.uid() = user_id);

-- ai_recommendation_favorites policies
drop policy if exists "Users can read own favorites" on public.ai_recommendation_favorites;
create policy "Users can read own favorites"
on public.ai_recommendation_favorites
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own favorites" on public.ai_recommendation_favorites;
create policy "Users can insert own favorites"
on public.ai_recommendation_favorites
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own favorites" on public.ai_recommendation_favorites;
create policy "Users can delete own favorites"
on public.ai_recommendation_favorites
for delete
using (auth.uid() = user_id);

-- ai_generation_records policies
drop policy if exists "Users can read own generation records" on public.ai_generation_records;
create policy "Users can read own generation records"
on public.ai_generation_records
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own generation records" on public.ai_generation_records;
create policy "Users can insert own generation records"
on public.ai_generation_records
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own generation records" on public.ai_generation_records;
create policy "Users can delete own generation records"
on public.ai_generation_records
for delete
using (auth.uid() = user_id);

-- ai_generation_favorites policies
drop policy if exists "Users can read own generation favorites" on public.ai_generation_favorites;
create policy "Users can read own generation favorites"
on public.ai_generation_favorites
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own generation favorites" on public.ai_generation_favorites;
create policy "Users can insert own generation favorites"
on public.ai_generation_favorites
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own generation favorites" on public.ai_generation_favorites;
create policy "Users can delete own generation favorites"
on public.ai_generation_favorites
for delete
using (auth.uid() = user_id);

-- search_history policies
drop policy if exists "Users can read own search history" on public.search_history;
create policy "Users can read own search history"
on public.search_history
for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own search history" on public.search_history;
create policy "Users can insert own search history"
on public.search_history
for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own search history" on public.search_history;
create policy "Users can delete own search history"
on public.search_history
for delete
using (auth.uid() = user_id);
