from sqlalchemy import String, Table, Column

from app.database import Base


student_table = Table(
    "students",
    Base.metadata,
    Column("student_id", String(20), primary_key=True),
)