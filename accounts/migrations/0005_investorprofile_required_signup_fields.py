from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_alter_investorprofile_experience_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="investorprofile",
            name="first_name",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="last_name",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="address",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="address2",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="city",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="state",
            field=models.CharField(blank=True, max_length=2, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="zip_code",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="phone",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="investorprofile",
            name="ssn",
            field=models.CharField(blank=True, max_length=11, null=True),
        ),
    ]

