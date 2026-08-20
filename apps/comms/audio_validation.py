"""Authoritative WAV validation for manual uploads (spec 3A.4).

Content decides acceptance — never the filename or the browser MIME
declaration. Limits are the same code-owned settings the seed importer uses.
"""

import hashlib
import io
import wave
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings


class AudioValidationError(Exception):
    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass(frozen=True)
class WavInfo:
    byte_size: int
    duration_seconds: Decimal
    sample_rate: int
    channels: int
    sha256: str


def validate_wav(data: bytes) -> WavInfo:
    if not data:
        raise AudioValidationError("empty_file", "The uploaded file is empty.")
    if len(data) > settings.SEED_MAX_WAV_BYTES:
        limit_mb = settings.SEED_MAX_WAV_BYTES // (1024 * 1024)
        raise AudioValidationError("oversize", f"The recording exceeds the {limit_mb} MB limit.")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioValidationError("invalid_signature", "The file is not a RIFF/WAVE recording.")
    try:
        with wave.open(io.BytesIO(data)) as parsed:
            channels = parsed.getnchannels()
            sample_width = parsed.getsampwidth()
            rate = parsed.getframerate()
            frames = parsed.getnframes()
            # Force a full read so a truncated data chunk fails here, not later.
            parsed.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise AudioValidationError(
            "invalid_container", "The WAV container is malformed or uses a compressed codec."
        ) from error
    if channels not in (1, 2):
        raise AudioValidationError("unsupported_channels", "Only mono or stereo is supported.")
    if sample_width not in (1, 2, 3, 4):
        raise AudioValidationError("unsupported_width", "Unsupported PCM sample width.")
    if rate <= 0 or frames <= 0:
        raise AudioValidationError("no_audio", "The recording contains no audio frames.")
    duration = Decimal(frames) / Decimal(rate)
    if duration > settings.SEED_MAX_WAV_SECONDS:
        raise AudioValidationError(
            "too_long",
            f"The recording exceeds the {settings.SEED_MAX_WAV_SECONDS}-second limit.",
        )
    return WavInfo(
        byte_size=len(data),
        duration_seconds=duration.quantize(Decimal("0.01")),
        sample_rate=rate,
        channels=channels,
        sha256=hashlib.sha256(data).hexdigest(),
    )
