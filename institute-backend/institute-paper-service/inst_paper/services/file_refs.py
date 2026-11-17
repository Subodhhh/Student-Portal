"""Who still uses a stored file. The paper service is the only writer of the file store and stored_files is its only registry of
storage keys; every record that uses a file points at stored_files.file_id with a foreign key (uploads and their answer keys, photo
draft pages, review drafts ...). The referencing columns are read from the schema, never from a list, so a table added later is
respected without changing this module."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.database import Base

TARGET = "stored_files.file_id"


def referencing_columns() -> list[tuple]:
    return [(table, col) for table in Base.metadata.sorted_tables for col in table.columns
            if any(fk.target_fullname == TARGET for fk in col.foreign_keys)]


def references(db: Session, file_id: UUID, *, ignore: tuple[str, str, object] | None = None) -> list[str]:
    """Every "table.column" pointing at the file. `ignore` = (table, key column, key value): one row not to count (the row being
    removed together with the file)."""
    found = []
    for table, col in referencing_columns():
        q = select(func.count()).select_from(table).where(col == file_id)
        if ignore and table.name == ignore[0]:
            q = q.where(table.c[ignore[1]] != ignore[2])
        if db.scalar(q):
            found.append(f"{table.name}.{col.name}")
    return found
