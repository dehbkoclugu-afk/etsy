from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.password_validation import validate_password

from pinforge_web.models import User


class SignUpForm(forms.Form):
    email = forms.EmailField(max_length=254)
    organization_name = forms.CharField(max_length=120, label="Shop name")
    timezone = forms.CharField(max_length=64, initial="UTC")
    password1 = forms.CharField(
        widget=forms.PasswordInput,
        validators=(validate_password,),
    )
    password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self) -> str:
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_timezone(self) -> str:
        timezone = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise forms.ValidationError("Enter a valid IANA timezone.") from error
        return timezone

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        return cleaned
