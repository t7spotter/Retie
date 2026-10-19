from django import forms
from django.db import IntegrityError, transaction

from .models import Link, Tag, generate_short_code, validate_alias, validate_destination


class LinkForm(forms.ModelForm):
    """Create/edit form shared by the dashboard views and HTMX handlers."""

    tags = forms.CharField(
        required=False,
        help_text="Comma-separated labels, e.g. docs, marketing",
        widget=forms.TextInput(attrs={"placeholder": "optional, comma-separated tags"}),
    )

    def __init__(self, *args, user=None, instance=None, **kwargs):
        self.user = user
        self.tag_names = []
        if instance is not None and instance.pk:
            kwargs["initial"] = {
                **(kwargs.get("initial") or {}),
                "tags": ", ".join(
                    instance.tags.order_by("name").values_list("name", flat=True)
                ),
            }
        super().__init__(*args, instance=instance, **kwargs)

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

    def clean_tags(self):
        raw = self.cleaned_data.get("tags") or ""
        from .models import normalize_tag_names

        self.tag_names = normalize_tag_names(raw)
        return raw

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
            for name in self.tag_names:
                tag, _ = Tag.objects.get_or_create(user=self.user, name=name)
                link.tags.add(tag)
            # Drop tags that are no longer named on the form.
            for tag in list(link.tags.all()):
                if tag.name not in self.tag_names:
                    link.tags.remove(tag)
        return link
