from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_alter_subscription_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="plan",
            field=models.CharField(
                choices=[
                    ("starter", "Starter"),
                    ("pro", "Pro"),
                    ("business", "Business"),
                ],
                default="starter",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="provider",
            field=models.CharField(default="mercadopago", max_length=30),
        ),
    ]
