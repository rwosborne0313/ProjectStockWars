from django.conf import settings
from django.db import models


class AgeBracket(models.TextChoices):
    AGE_18_24 = "18_24", "18-24"
    AGE_25_34 = "25_34", "25-34"
    AGE_35_44 = "35_44", "35-44"
    AGE_45_54 = "45_54", "45-54"
    AGE_55_64 = "55_64", "55-64"
    AGE_65_74 = "65_74", "65-74"
    AGE_75_84 = "75_84", "75-84"
    AGE_85_94 = "85_94", "85-94"
    AGE_95_104 = "95_104", "95-104"


class ExperienceLevel(models.TextChoices):
    BEGINNER = "BEGINNER", "Beginner"
    INTERMEDIATE = "INTERMEDIATE", "Intermediate"
    ADVANCED = "ADVANCED", "Advanced"
    PRO_TRADER = "PRO_TRADER", "Pro Trader"


US_STATES = [
    ("AL", "AL"),
    ("AK", "AK"),
    ("AZ", "AZ"),
    ("AR", "AR"),
    ("CA", "CA"),
    ("CO", "CO"),
    ("CT", "CT"),
    ("DE", "DE"),
    ("FL", "FL"),
    ("GA", "GA"),
    ("HI", "HI"),
    ("ID", "ID"),
    ("IL", "IL"),
    ("IN", "IN"),
    ("IA", "IA"),
    ("KS", "KS"),
    ("KY", "KY"),
    ("LA", "LA"),
    ("ME", "ME"),
    ("MD", "MD"),
    ("MA", "MA"),
    ("MI", "MI"),
    ("MN", "MN"),
    ("MS", "MS"),
    ("MO", "MO"),
    ("MT", "MT"),
    ("NE", "NE"),
    ("NV", "NV"),
    ("NH", "NH"),
    ("NJ", "NJ"),
    ("NM", "NM"),
    ("NY", "NY"),
    ("NC", "NC"),
    ("ND", "ND"),
    ("OH", "OH"),
    ("OK", "OK"),
    ("OR", "OR"),
    ("PA", "PA"),
    ("RI", "RI"),
    ("SC", "SC"),
    ("SD", "SD"),
    ("TN", "TN"),
    ("TX", "TX"),
    ("UT", "UT"),
    ("VT", "VT"),
    ("VA", "VA"),
    ("WA", "WA"),
    ("WV", "WV"),
    ("WI", "WI"),
    ("WY", "WY"),
    ("DC", "DC"),
]


class InvestorProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    age_bracket = models.CharField(max_length=16, choices=AgeBracket.choices)
    experience_level = models.CharField(max_length=16, choices=ExperienceLevel.choices)
    display_name = models.CharField(max_length=32, unique=True)

    # KYC / account profile fields (required at signup, nullable for legacy rows)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    address2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=120, blank=True, null=True)
    state = models.CharField(max_length=2, choices=US_STATES, blank=True, null=True)
    zip_code = models.CharField(max_length=10, blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    ssn = models.CharField(max_length=11, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.display_name
