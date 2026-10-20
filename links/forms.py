from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Link, Tag, generate_short_code, validate_alias, validate_destination


class LinkForm(forms.ModelForm):
    """Create/edit form shared by the dashboard views and HTMX handlers."""

    tags = forms.CharField(
        required=False,
        help_text="Comma-separated labels, e.g. docs, marketing",
        widget=forms.TextInput(attrs={"placeholder": "optional, comma-separated tags"}),
    )
    # Write-only gate password. Empty means "no change" on edit; the
    # checkbox clears an existing password. The hash is stored, never
    # the password itself.
    password = forms.CharField(
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"placeholder": "unchanged", "autocomplete": "new-password"}
        ),
        help_text="Visitors must enter this once per browser session.",
    )
    remove_password = forms.BooleanField(required=False)
    # UTM builder: appended to the destination at save time. Sending empty
    # values (an edit without touching these) changes nothing — previously
    # stored UTM parameters are never stripped.
    utm_source = forms.CharField(required=False, strip=True, max_length=120)
    utm_medium = forms.CharField(required=False, strip=True, max_length=120)
    utm_campaign = forms.CharField(required=False, strip=True, max_length=120)
    # Hotlink protection allow-list: comma/semicolon separated domains.
    # Normalized (lowercased, stripped of schemes/paths) and stored
    # newline-separated. Empty means "allow from anywhere".
    allowed_referrers = forms.CharField(required=False, strip=True)
    # Set by the "create anyway" button after a duplicate-destination
    # warning; never rendered on the initial form.
    confirm_duplicate = forms.BooleanField(required=False)

    # Multi-domain: one of the hostnames configured via EXTRA_DOMAINS
    # (plus the SITE_URL host itself); "" = the default domain.
    domain = forms.ChoiceField(required=False)

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
        # Domain choices come from configuration, so they exist per-form.
        from urllib.parse import urlsplit
        from django.conf import settings

        default_host = urlsplit(settings.SITE_URL).hostname
        hosts = [default_host or ""] + [
            _d for _d in settings.EXTRA_DOMAINS if _d != default_host
        ]
        self.fields["domain"].choices = [("", "Default domain")] + [(_h, _h) for _h in hosts]

    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        # Browser datetime-local sends "2025-01-31T14:30"; also accept a
        # space-separated variant for curl/HTMX convenience.
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )

    goes_live_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )
    # A/B split-testing: optional second destination. Validated with the
    # same rules as the main destination (scheme, blocklist).
    variant_b = forms.CharField(required=False, strip=True)
    # QR styling: hex colors like #0e353c. Optional — empty falls back to
    # the server defaults when the SVG is rendered.
    qr_dark = forms.CharField(required=False, strip=True)
    qr_light = forms.CharField(required=False, strip=True)

    class Meta:
        model = Link
        fields = [
            "destination",
            "title",
            "code",
            "expires_at",
            "goes_live_at",
            "variant_b",
            "qr_dark",
            "qr_light",
            "domain",
            "is_active",
            "permanent",
            "public_stats",
            "public_hide_countries",
            "notes",
        ]
        widgets = {
            "destination": forms.URLInput(attrs={"placeholder": "https://example.com/article"}),
            "title": forms.TextInput(attrs={"placeholder": "Optional title"}),
            "code": forms.TextInput(attrs={"placeholder": "Optional custom alias"}),
            "notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Private note. Only you see this."}),
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

    def clean(self):
        cleaned = super().clean()
        # UTM builder: fold the optional UTM_* fields into the destination
        # query string, preserving parameters already present there.
        utm_parts = {
            f"utm_{key}": value
            for key in ("source", "medium", "campaign")
            for value in [cleaned.get(f"utm_{key}") or ""]
            if value
        }
        if utm_parts and cleaned.get("destination"):
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

            split = urlsplit(cleaned["destination"])
            existing = parse_qsl(split.query, keep_blank_values=True)
            merged = dict(existing)
            merged.update(utm_parts)
            cleaned["destination"] = urlunsplit(
                split._replace(query=urlencode(merged, doseq=True))
            )
        # Referrer allow-list: normalize now so a bad entry becomes a field
        # error instead of an exception at save time.
        from .models import normalize_referrer_domains, validate_destination

        cleaned["allowed_referrers"] = "\n".join(
            normalize_referrer_domains(cleaned.get("allowed_referrers") or "")
        )
        # Variant B: same rules as the main destination; attribute errors
        # to the field so the form redisplay shows them next to the input.
        if cleaned.get("variant_b"):
            from .models import validate_destination

            try:
                cleaned["variant_b"] = validate_destination(cleaned["variant_b"])
            except ValidationError:
                self.add_error(
                    "variant_b", "Enter a valid http:// or https:// URL."
                )
        # QR colors: accept and store as lowercase 6-digit hex with "#".
        import re

        for name in ("qr_dark", "qr_light"):
            value = cleaned.get(name)
            if not value:
                cleaned[name] = ""
                continue
            if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
                self.add_error(name, "Use a 6-digit hex color like #0e353c.")
            else:
                cleaned[name] = f"#{value.lstrip('#').lower()}"
        return cleaned

    def _save_instance(self, link: Link, commit: bool) -> Link:
        if not commit:
            return link
        from django.contrib.auth.hashers import make_password

        password = self.cleaned_data.get("password") or ""
        if password:
            link.password_hash = make_password(password)
        elif self.cleaned_data.get("remove_password"):
            link.password_hash = ""
        # Normalize the referrer allow-list (may raise ValidationError with a
        # clean message).
        link.allowed_referrers = self.cleaned_data.get("allowed_referrers") or ""
        # construct_instance skips defaulted fields whose value is omitted
        # from the POST, so clearing variant B by sending nothing needs an
        # explicit assignment here.
        link.variant_b = self.cleaned_data.get("variant_b") or ""
        # QR colors likewise (both empty = fall back to server defaults
        # when the SVG is generated).
        link.qr_dark = self.cleaned_data.get("qr_dark") or ""
        link.qr_light = self.cleaned_data.get("qr_light") or ""
        # Domain pinning: only hosts from EXTRA_DOMAINS (+ SITE_URL host)
        # reach validated_data at all (ChoiceField); "" = default domain.
        link.domain = self.cleaned_data.get("domain") or ""
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
