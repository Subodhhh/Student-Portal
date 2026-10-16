from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects.postgresql import UUID


metadata = MetaData()


providers = Table(
    "providers",
    metadata,
    Column(
        "provider_id",
        UUID(as_uuid=True),
        primary_key=True,
    ),
)


branches = Table(
    "branches",
    metadata,
    Column(
        "branch_id",
        UUID(as_uuid=True),
        primary_key=True,
    ),
)