-- Cover owner-scoped review lookups and user foreign keys reported by the
-- Supabase performance advisor.

create index if not exists chess_fen_review_sessions_owner_source_updated_idx
    on public.chess_fen_review_sessions (
        owner_user_id,
        source_document_sha256,
        updated_at desc
    );

create index if not exists chess_fen_review_sessions_closed_by_idx
    on public.chess_fen_review_sessions (closed_by_user_id)
    where closed_by_user_id is not null;

create index if not exists chess_fen_dataset_versions_owner_created_idx
    on public.chess_fen_dataset_versions (owner_user_id, created_at desc);
