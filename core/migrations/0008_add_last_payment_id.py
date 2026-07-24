from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_add_domain_purchase_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='last_payment_id',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
