"""The single service-owned content-fingerprint formula (spec 2B).

Every adapter — dataset seed, manual email paste, manual WAV upload, future
simulation — uses these functions. Adapters parse structure but may never
implement a competing normalization, so a manual paste of a dataset email's
text produces the identical fingerprint ("matches existing dataset email").
"""

import hashlib
import unicodedata

_EMAIL_NAMESPACE = "email:v1"
_AUDIO_NAMESPACE = b"audio-bytes:v1"


def canonical_email_address(raw: str) -> str:
    """NFKC-normalize, trim outer whitespace, and case-fold a sender address."""
    return unicodedata.normalize("NFKC", raw).strip().casefold()


def canonical_text(raw: str) -> str:
    """Canonicalize subject/body text for duplicate detection only.

    NFKC normalization, CRLF/CR line endings become LF, outer whitespace is
    trimmed, and horizontal trailing whitespace is removed per line. Case and
    paragraph boundaries are retained — the original content is never replaced.
    """
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    return "\n".join(lines).strip()


def email_fingerprint(sender: str, subject: str, body: str) -> str:
    payload = "\0".join(
        [
            _EMAIL_NAMESPACE,
            canonical_email_address(sender),
            canonical_text(subject),
            canonical_text(body),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audio_fingerprint(raw_wav_bytes: bytes) -> str:
    return hashlib.sha256(_AUDIO_NAMESPACE + b"\0" + raw_wav_bytes).hexdigest()
