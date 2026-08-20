"""The one service-owned fingerprint formula (spec 2B) used by every adapter.

Email v1:  SHA-256(UTF-8("email:v1\\0" + canonical_sender + "\\0" + canonical_subject
                        + "\\0" + canonical_body))
Audio v1:  SHA-256(b"audio-bytes:v1\\0" + raw_wav_bytes)
"""

import json
from pathlib import Path

from apps.comms.fingerprints import (
    audio_fingerprint,
    canonical_email_address,
    canonical_text,
    email_fingerprint,
)

DATASET = Path(__file__).resolve().parent.parent / "goodlane-dataset"


class TestCanonicalization:
    def test_sender_is_nfkc_trimmed_and_casefolded(self):
        assert (
            canonical_email_address("  Desmond@AtlanticCarriersInc.com \n")
            == "desmond@atlanticcarriersinc.com"
        )

    def test_text_normalizes_line_endings_to_lf(self):
        assert canonical_text("line one\r\nline two\rline three") == (
            "line one\nline two\nline three"
        )

    def test_text_strips_trailing_horizontal_whitespace_per_line(self):
        assert canonical_text("rate is $400  \nconfirm\t\n") == "rate is $400\nconfirm"

    def test_text_retains_case_and_paragraph_boundaries(self):
        original = "Rate CONFIRMED.\n\nSecond paragraph."
        assert canonical_text(original) == original

    def test_text_applies_nfkc(self):
        # The ﬁ ligature decomposes to "fi" under NFKC.
        assert canonical_text("conﬁrm") == "confirm"


class TestEmailFingerprint:
    def test_deterministic_64_char_lowercase_hex(self):
        fp = email_fingerprint("a@b.com", "subject", "body")
        assert fp == email_fingerprint("a@b.com", "subject", "body")
        assert len(fp) == 64
        assert fp == fp.lower()
        int(fp, 16)

    def test_formatting_noise_does_not_change_identity(self):
        clean = email_fingerprint("a@b.com", "Load 29372450", "Can do. What's the all-in?")
        noisy = email_fingerprint(
            " A@B.COM ", "Load 29372450  ", "Can do. What's the all-in?  \r\n"
        )
        assert clean == noisy

    def test_body_case_change_is_a_different_email(self):
        assert email_fingerprint("a@b.com", "s", "body") != email_fingerprint(
            "a@b.com", "s", "BODY"
        )

    def test_subject_and_body_do_not_collide_across_the_separator(self):
        assert email_fingerprint("a@b.com", "xy", "z") != email_fingerprint(
            "a@b.com", "x", "yz"
        )

    def test_manual_paste_of_dataset_email_matches_dataset_fingerprint(self):
        emails = json.loads((DATASET / "carrier_emails.json").read_text())
        record = next(e for e in emails if e["email_id"] == "CE0074")
        dataset_fp = email_fingerprint(record["from_email"], record["subject"], record["body"])
        pasted_fp = email_fingerprint(
            "  " + record["from_email"].upper(),
            record["subject"] + "  ",
            record["body"].replace("\n", "\r\n") + "\n",
        )
        assert dataset_fp == pasted_fp


class TestAudioFingerprint:
    def test_same_bytes_same_identity(self):
        assert audio_fingerprint(b"RIFF....WAVE") == audio_fingerprint(b"RIFF....WAVE")

    def test_single_byte_difference_is_a_different_recording(self):
        assert audio_fingerprint(b"RIFF....WAVE") != audio_fingerprint(b"RIFF....WAVF")

    def test_email_and_audio_namespaces_never_collide(self):
        assert email_fingerprint("", "", "") != audio_fingerprint(b"")
