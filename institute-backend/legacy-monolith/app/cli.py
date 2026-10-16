"""Operator commands (run inside the backend container, never exposed over HTTP).

  python -m app.cli create-super-admin EMAIL "Full Name"

The only way to create the FIRST Super Admin. The password is read from the
INSTITUTE_ADMIN_PASSWORD environment variable or prompted for (never an argument, so it
stays out of shell history) and must satisfy the normal password policy. The email is
marked verified because an operator with database-level access is vouching for it; the
account still needs email OTP at every login."""
import getpass
import os
import sys

from sqlalchemy import select

from app.core import audit
from app.core.passwords import hash_password, validate_password
from app.core.roles import Role
from app.database import SessionLocal, utcnow
from app.models.staff import Staff, StaffRoleAssignment
from app.services.codes import next_code


def create_super_admin(email: str, full_name: str, password: str) -> str:
    problems = validate_password(password, personal=[email.split("@")[0], full_name])
    if problems:
        raise SystemExit("Password rejected: " + " ".join(problems))
    with SessionLocal() as db:
        if db.scalar(select(Staff).where(Staff.email == email.lower())):
            raise SystemExit("A staff identity with this email already exists; grant the role via the API instead.")
        staff = Staff(staff_code=next_code(db, Staff.staff_code, "STF"), email=email.lower(), full_name=full_name,
                      password_hash=hash_password(password), password_changed_at=utcnow(), email_verified_at=utcnow())
        db.add(staff)
        db.flush()
        db.add(StaffRoleAssignment(staff_id=staff.staff_id, role=Role.SUPER_ADMIN.value))
        db.commit()
        audit.record(db, "staff.bootstrap_super_admin", "SUCCESS", target_type="staff", target_id=staff.staff_id,
                     details={"via": "cli"})
        return staff.staff_code


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "create-super-admin":
        raise SystemExit(__doc__)
    pw = os.environ.get("INSTITUTE_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    print(f"Super Admin created. Staff ID: {create_super_admin(sys.argv[2], sys.argv[3], pw)}")
