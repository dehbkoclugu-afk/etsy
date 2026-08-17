from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from pinforge_web.uploads import (
    creative_asset_upload_to,
    listing_image_upload_to,
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ) -> User:
        if not email:
            raise ValueError("email is required")
        normalized = self.normalize_email(email).lower()
        user = self.model(email=normalized, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str,
        **extra_fields: Any,
    ) -> User:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("superuser must have is_staff and is_superuser")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default="UTC")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "organization"),
                name="unique_user_organization_membership",
            )
        ]


class TimestampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ProviderConnection(TimestampedUUIDModel):
    class Provider(models.TextChoices):
        ETSY = "etsy", "Etsy"
        PINTEREST = "pinterest", "Pinterest"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="provider_connections",
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    external_account_id = models.CharField(max_length=128)
    token_ciphertext = models.TextField()
    scopes = models.JSONField(default=list)
    token_expires_at = models.DateTimeField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("provider", "external_account_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "external_account_id"),
                name="unique_provider_account_connection",
            )
        ]


class Shop(TimestampedUUIDModel):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        ETSY = "etsy", "Etsy"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="shops",
    )
    connection = models.ForeignKey(
        ProviderConnection,
        on_delete=models.SET_NULL,
        related_name="shops",
        null=True,
        blank=True,
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    source_shop_id = models.CharField(max_length=128)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "source", "source_shop_id"),
                name="unique_shop_source_per_organization",
            )
        ]


class Listing(TimestampedUUIDModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    source_listing_id = models.CharField(max_length=128)
    title = models.CharField(max_length=500)
    listing_url = models.URLField(max_length=2_000)
    price_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    tags = models.JSONField(default=list)
    description = models.TextField(blank=True)
    vertical = models.CharField(max_length=64, default="general")
    active = models.BooleanField(default=True)
    synchronized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "shop", "source_listing_id"),
                name="unique_listing_source_per_shop",
            ),
            models.CheckConstraint(
                condition=models.Q(price_minor__gte=0),
                name="listing_price_minor_non_negative",
            ),
        ]


class ListingImage(TimestampedUUIDModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="listing_images",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="images",
    )
    file = models.FileField(upload_to=listing_image_upload_to, max_length=500)
    position = models.PositiveSmallIntegerField()
    mime_type = models.CharField(max_length=32)
    byte_size = models.BigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("listing", "position"),
                name="unique_listing_image_position",
            ),
            models.CheckConstraint(
                condition=models.Q(byte_size__gte=0),
                name="listing_image_size_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(width__gt=0, height__gt=0),
                name="listing_image_dimensions_positive",
            ),
        ]


class BrandKit(TimestampedUUIDModel):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="brand_kit",
    )
    shop_name = models.CharField(max_length=120)
    primary = models.CharField(max_length=7, default="#173C35")
    accent = models.CharField(max_length=7, default="#C9855B")
    surface = models.CharField(max_length=7, default="#F4F0E6")
    ink = models.CharField(max_length=7, default="#18201D")
    headline_font = models.CharField(
        max_length=64,
        default="DejaVuSerif-Bold.ttf",
    )
    body_font = models.CharField(max_length=64, default="DejaVuSans.ttf")
    body_bold_font = models.CharField(
        max_length=64,
        default="DejaVuSans-Bold.ttf",
    )


class Creative(TimestampedUUIDModel):
    class Template(models.TextChoices):
        MOCKUP_HERO = "mockup_hero", "Mockup Hero"
        LIST_STACK = "list_stack", "List Stack"
        SPLIT_COMPARE = "split_compare", "Split Compare"
        TEXT_OVERLAY = "text_overlay", "Text Overlay"
        GRID_PREVIEW = "grid_preview", "Grid Preview"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RENDERING = "rendering", "Rendering"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="creatives",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="creatives",
    )
    template_id = models.CharField(max_length=32, choices=Template.choices)
    title = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    alt_text = models.CharField(max_length=500)
    destination_url = models.URLField(max_length=2_000)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    input_revision = models.CharField(max_length=64, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ("-created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "listing", "input_revision"),
                condition=~models.Q(input_revision=""),
                name="unique_creative_input_revision",
            )
        ]


class CreativeAsset(TimestampedUUIDModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="creative_assets",
    )
    creative = models.OneToOneField(
        Creative,
        on_delete=models.CASCADE,
        related_name="asset",
    )
    file = models.FileField(upload_to=creative_asset_upload_to, max_length=500)
    mime_type = models.CharField(max_length=32, default="image/png")
    byte_size = models.BigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(byte_size__gte=0),
                name="creative_asset_size_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(width__gt=0, height__gt=0),
                name="creative_asset_dimensions_positive",
            ),
        ]


class PinterestBoard(TimestampedUUIDModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="pinterest_boards",
    )
    connection = models.ForeignKey(
        ProviderConnection,
        on_delete=models.CASCADE,
        related_name="pinterest_boards",
    )
    external_board_id = models.CharField(max_length=128)
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=True)
    synchronized_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "external_board_id"),
                name="unique_pinterest_board_per_connection",
            )
        ]


class PinPublication(TimestampedUUIDModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PUBLISHING = "publishing", "Publishing"
        PUBLISHED = "published", "Published"
        PUBLISH_UNKNOWN = "publish_unknown", "Needs review"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="pin_publications",
    )
    creative = models.ForeignKey(
        Creative,
        on_delete=models.CASCADE,
        related_name="publications",
    )
    connection = models.ForeignKey(
        ProviderConnection,
        on_delete=models.PROTECT,
        related_name="pin_publications",
    )
    board = models.ForeignKey(
        PinterestBoard,
        on_delete=models.PROTECT,
        related_name="pin_publications",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    scheduled_at = models.DateTimeField(default=timezone.now)
    remote_pin_id = models.CharField(max_length=128, blank=True)
    remote_url = models.URLField(max_length=2_000, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("-scheduled_at", "-created_at", "id")


class Job(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        RENDER_CREATIVE = "render_creative", "Render creative"
        SYNC_ETSY = "sync_etsy", "Sync Etsy listings"
        PUBLISH_PINTEREST = "publish_pinterest", "Publish to Pinterest"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        RETRY = "retry", "Retry"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    priority = models.SmallIntegerField(default=100)
    due_at = models.DateTimeField(default=timezone.now)
    lease_owner = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    deduplication_key = models.CharField(max_length=160)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("priority", "due_at", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "kind", "deduplication_key"),
                name="unique_job_deduplication_key",
            ),
            models.CheckConstraint(
                condition=models.Q(attempts__gte=0),
                name="job_attempts_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1),
                name="job_max_attempts_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "due_at", "priority"),
                name="job_claim_order_idx",
            )
        ]
