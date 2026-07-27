create table if not exists sessions (
    id text primary key,
    title text not null,
    created_at text not null,
    updated_at text not null,
    metadata text not null default '{}'
);

create table if not exists messages (
    id integer primary key autoincrement,
    session_id text not null references sessions(id) on delete cascade,
    role text not null,
    content text not null,
    created_at text not null,
    metadata text not null default '{}'
);
