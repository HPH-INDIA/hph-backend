from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


class InvalidCredentialsError(Exception):
    """A password did not match its stored hash, or the hash was malformed.

    Callers must treat both cases identically — surfacing which one occurred
    would leak whether a stored hash exists/is well-formed for a given account.
    """


def hash_password(plaintext):
    return _hasher.hash(plaintext)


def verify_password(password_hash, plaintext):
    if not password_hash:
        raise InvalidCredentialsError()
    try:
        _hasher.verify(password_hash, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise InvalidCredentialsError() from None
