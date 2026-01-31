from django.db import models

# Create your models here.


class Sponsor(models.Model):
    name = models.CharField(max_length=200, unique=True)
    logo = models.FileField(upload_to="sponsors/logos/", blank=True, null=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)

    # Admin-only field
    contact_email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
