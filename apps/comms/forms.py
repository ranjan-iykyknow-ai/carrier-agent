"""Manual Ingestion Lab forms (spec 3A.3/3A.4).

Server-side validation is authoritative; browser attributes are usability hints.
Field limits are code-owned constants enforced here and mirrored in templates.
"""

import re

from django import forms

from apps.comms.audio_validation import AudioValidationError, validate_wav

MAX_SENDER_NAME = 255
MAX_SENDER_EMAIL = 320
MAX_SUBJECT = 998
MAX_BODY = 50_000
MAX_LOAD_HINT = 64
MAX_CARRIER_HINT = 255

# NUL and unsafe control characters are rejected; \t \n \r stay legitimate text.
_UNSAFE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _reject_control_characters(value: str) -> str:
    if value and _UNSAFE_CONTROL.search(value):
        raise forms.ValidationError("Control characters are not allowed.")
    return value


class ManualEmailForm(forms.Form):
    sender_name = forms.CharField(required=False, max_length=MAX_SENDER_NAME, strip=True)
    sender_email = forms.EmailField(max_length=MAX_SENDER_EMAIL)
    subject = forms.CharField(required=False, max_length=MAX_SUBJECT, strip=True)
    body = forms.CharField(max_length=MAX_BODY, strip=False, widget=forms.Textarea)
    suspected_load_reference = forms.CharField(required=False, max_length=MAX_LOAD_HINT)
    suspected_carrier_identity = forms.CharField(required=False, max_length=MAX_CARRIER_HINT)

    def clean_sender_name(self):
        return _reject_control_characters(self.cleaned_data["sender_name"])

    def clean_subject(self):
        return _reject_control_characters(self.cleaned_data["subject"])

    def clean_body(self):
        body = _reject_control_characters(self.cleaned_data["body"])
        if not body.strip():
            raise forms.ValidationError("The email body cannot be empty.")
        return body

    def clean_suspected_load_reference(self):
        return _reject_control_characters(self.cleaned_data["suspected_load_reference"]).strip()

    def clean_suspected_carrier_identity(self):
        return _reject_control_characters(self.cleaned_data["suspected_carrier_identity"]).strip()


class ManualAudioForm(forms.Form):
    audio_file = forms.FileField()

    def clean_audio_file(self):
        upload = self.cleaned_data["audio_file"]
        if not upload.name.lower().endswith(".wav"):
            raise forms.ValidationError("Only PCM .wav recordings are accepted.")
        declared = (upload.content_type or "").lower()
        # A generic declaration may proceed when the bytes prove valid WAV;
        # an explicitly unrelated MIME type is rejected outright.
        if declared and declared not in (
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/vnd.wave",
            "application/octet-stream",
        ):
            raise forms.ValidationError("The uploaded file does not declare WAV audio.")
        data = upload.read()
        upload.seek(0)
        try:
            self.wav_info = validate_wav(data)
        except AudioValidationError as error:
            raise forms.ValidationError(error.summary) from error
        return upload
