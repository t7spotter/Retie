from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

# One input look for the auth pages so fields line up with the full-width
# submit buttons (Django's default widgets render browser-default widths,
# which sit narrower than a w-full button).
FIELD_CLASS = (
    "w-full rounded-xl border border-stone-300 bg-white px-3 py-2.5 text-sm "
    "outline-none transition-colors focus:border-indigo-400 focus:ring-2 "
    "focus:ring-indigo-100 dark:border-stone-700 dark:bg-stone-950 "
    "dark:text-stone-100 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
)


class _StyleMixin:
    """Apply FIELD_CLASS to text-like inputs so auth forms match the UI."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.TextInput, forms.EmailInput, forms.PasswordInput)):
                field.widget.attrs["class"] = FIELD_CLASS


class RetieLoginForm(_StyleMixin, AuthenticationForm):
    """AuthenticationForm with project-styled inputs."""


class RegisterForm(_StyleMixin, UserCreationForm):
    email = forms.EmailField(required=True, help_text="Used for password reset.")

    class Meta(UserCreationForm.Meta):
        fields = ["username", "email"]

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user
