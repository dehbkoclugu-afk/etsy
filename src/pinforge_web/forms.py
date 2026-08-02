from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import RegexValidator

from pinforge_web.models import BrandKit, Creative, User


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(
        self,
        data: UploadedFile | list[UploadedFile] | None,
        initial: object = None,
    ) -> list[UploadedFile]:
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)]


class ListingForm(forms.Form):
    title = forms.CharField(max_length=500)
    listing_url = forms.URLField(
        max_length=2_000,
        label="Etsy listing URL",
        assume_scheme="https",
    )
    price = forms.DecimalField(min_value=0, max_digits=18, decimal_places=2)
    currency = forms.CharField(max_length=3, initial="USD")
    tags = forms.CharField(
        required=False,
        help_text="Comma-separated; up to 13 tags.",
    )
    description = forms.CharField(
        required=False,
        max_length=20_000,
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    vertical = forms.CharField(max_length=64, initial="general")
    images = MultipleFileField(
        help_text="Upload 1 to 5 PNG or JPEG files, up to 15 MB each.",
    )

    def clean_tags(self) -> list[str]:
        return [
            value.strip()
            for value in self.cleaned_data["tags"].split(",")
            if value.strip()
        ]

    def clean_images(self) -> list[UploadedFile]:
        images: list[UploadedFile] = self.cleaned_data["images"]
        if not 1 <= len(images) <= 5:
            raise forms.ValidationError("Upload 1 to 5 images.")
        return images


hex_color = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message="Enter a six-digit hex color such as #173C35.",
)
FONT_CHOICES = (
    ("DejaVuSerif-Bold.ttf", "DejaVu Serif Bold"),
    ("DejaVuSans.ttf", "DejaVu Sans"),
    ("DejaVuSans-Bold.ttf", "DejaVu Sans Bold"),
)


class BrandKitForm(forms.ModelForm):
    primary = forms.CharField(max_length=7, validators=(hex_color,))
    accent = forms.CharField(max_length=7, validators=(hex_color,))
    surface = forms.CharField(max_length=7, validators=(hex_color,))
    ink = forms.CharField(max_length=7, validators=(hex_color,))
    headline_font = forms.ChoiceField(choices=FONT_CHOICES)
    body_font = forms.ChoiceField(choices=FONT_CHOICES)
    body_bold_font = forms.ChoiceField(choices=FONT_CHOICES)

    class Meta:
        model = BrandKit
        fields = (
            "shop_name",
            "primary",
            "accent",
            "surface",
            "ink",
            "headline_font",
            "body_font",
            "body_bold_font",
        )


class CreativeForm(forms.Form):
    template_id = forms.ChoiceField(
        choices=Creative.Template.choices,
        label="Template",
    )
    title = forms.CharField(max_length=100)
    description = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    alt_text = forms.CharField(
        max_length=500,
        help_text="Describe the visual for accessibility and Pinterest search.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


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
