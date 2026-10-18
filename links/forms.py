from django import forms
from django.db import IntegrityError, transaction

from .models import Link, generate_short_code, validate_alias, validate_destination


class LinkForm(forms.ModelForm):
    """Create/edit form shared by the dashboard views and HTMX handlers."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Used by save() to attach ownership; not part of the form itself.
        self.user = user

    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        # Browser datetime-local sends "2025-01-31T14:30"; also accept a
        # space-separated variant for curl/HTMX convenience.
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )

    class Meta:
        model = Link
        fields = ["destination", "title", "code", "expires_at", "is_active"]
        widgets = {
            "destination": forms.URLInput(attrs={"placeholder": "https://example.com/article"}),
            "title": forms.TextInput(attrs={"placeholder": "Optional title"}),
            "code": forms.TextInput(attrs={"placeholder": "Optional custom alias"}),
        }

    def clean_destination(self):
        # Validate the RAW submitted value: the URLField would silently
        # coerce "example.com" to "http://example.com", which we treat as
        # ambiguous input rather than a valid destination.
        raw = self.data.get("destination", "")
        validate_destination(raw)
        return self.cleaned_data["destination"]

    def clean_code(self):
        code = self.cleaned_data["code"].strip().lower()
        if not code:
            return ""  # empty means "generate one"
        return validate_alias(code)

    def save(self, commit: bool = True) -> Link:
        """Assign a unique generated code when no alias was provided.

        Collisions for generated codes are handled by retrying against the
        database's unique constraint rather than trusting check-then-insert.
        """
        link = super().save(commit=False)
        link.user = self.user
        if link.code:
            return self._save_instance(link, commit)
        for _ in range(5):
            link.code = generate_short_code()
            try:
                return self._save_instance(link, commit)
            except IntegrityError:
                continue
        raise IntegrityError("Could not generate a unique short code.")

    def _save_instance(self, link: Link, commit: bool) -> Link:
        if not commit:
            return link
        with transaction.atomic():
            link.save()
        return link
