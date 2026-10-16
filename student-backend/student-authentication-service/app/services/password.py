from argon2 import PasswordHasher


# Argon2id is used for secure password hashing.
password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    # Convert a plain password into a secure hash.
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # Check whether the entered password matches the stored hash.
    try:
        password_hasher.verify(password_hash, password)
        return True
    except Exception:
        return False
