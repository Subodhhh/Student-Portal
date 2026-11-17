"""Display names of several kinds of record in ONE statement (each database round trip costs ~200 ms to the hosted database)."""
from sqlalchemy import literal, select, union_all
from sqlalchemy.orm import Session

from institute_common.models.academics import Subject
from institute_common.models.org import Batch, Branch, Provider
from institute_common.models.staff import Staff

_COLUMNS = {"providers": (Provider.provider_id, Provider.name), "branches": (Branch.branch_id, Branch.name),
            "batches": (Batch.batch_id, Batch.name), "subjects": (Subject.subject_id, Subject.name),
            "staff": (Staff.staff_id, Staff.full_name)}


def names(db: Session, **ids) -> dict[str, dict]:
    """names(db, providers={...}, branches={...}) -> {"providers": {id: name}, "branches": {id: name}}: the same rows as one
    `select(id, name).where(id.in_(ids))` per kind, in a single UNION ALL. A kind with no ids gets {} without being queried."""
    out = {kind: {} for kind in ids}
    parts = [select(literal(kind).label("kind"), col_id.label("id"), col_name.label("name")).where(col_id.in_(set(wanted)))
             for kind, wanted in ids.items() if wanted for col_id, col_name in [_COLUMNS[kind]]]
    if parts:
        for kind, id_, name in db.execute(parts[0] if len(parts) == 1 else union_all(*parts)):
            out[kind][id_] = name
    return out
