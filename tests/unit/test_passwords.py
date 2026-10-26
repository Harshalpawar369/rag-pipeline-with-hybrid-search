from app.security.passwords import DUMMY_HASH, hash_password, verify_password


def test_round_trip_accepts_the_right_password_only():
    stored = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", stored) is True
    assert verify_password("correct-horse-batterY", stored) is False


def test_same_password_hashes_differently_because_of_salt():
    assert hash_password("same-password-1") != hash_password("same-password-1")


def test_stored_format_names_the_scheme_and_parameters():
    stored = hash_password("some-password")
    scheme, n, r, p, salt, dk = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(dk)) == 32


def test_malformed_or_foreign_hashes_never_verify():
    assert verify_password("x", "garbage") is False
    assert verify_password("x", "") is False
    assert verify_password("x", "bcrypt$10$abc") is False
    assert verify_password("x", "scrypt$16384$8$1$zz$zz") is False


def test_dummy_hash_is_a_valid_hash_that_matches_no_common_password():
    assert verify_password("password", DUMMY_HASH) is False
    assert DUMMY_HASH.startswith("scrypt$")
