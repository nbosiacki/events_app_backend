"""
Password hashing utilities using Argon2id.

Argon2id is the winner of the Password Hashing Competition (2015) and is
recommended by OWASP for password storage. It's resistant to both GPU-based
attacks (due to memory-hardness) and side-channel attacks.

See README.md in this directory for detailed security explanations.
"""

from passlib.context import CryptContext

# Argon2id configuration following OWASP recommendations
# https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    # Memory cost: 64 MB - makes GPU attacks expensive
    argon2__memory_cost=65536,
    # Time cost: 3 iterations - balances security vs UX
    argon2__time_cost=3,
    # Parallelism: 4 threads
    argon2__parallelism=4,
)


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2id.

    Returns a string containing the algorithm parameters and hash,
    allowing automatic verification and parameter upgrades.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash.

    Uses constant-time comparison to prevent timing attacks.
    Returns True if password matches, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)
