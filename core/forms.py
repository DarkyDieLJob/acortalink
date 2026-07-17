from django import forms
from django.contrib.auth.forms import UserCreationForm


class ContactoAcortalinkForm(forms.Form):
    TIPO_CHOICES = [
        ('sugerencia', 'Sugerencia'),
        ('bug', 'Reporte de error / bug'),
        ('premium', 'Consulta premium'),
        ('otro', 'Otro'),
    ]

    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Tu nombre',
            'style': 'background:#111;border:1px solid #222;color:#fff;',
        })
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'tu@email.com',
            'style': 'background:#111;border:1px solid #222;color:#fff;',
        })
    )
    tipo = forms.ChoiceField(
        choices=TIPO_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-control',
            'style': 'background:#111;border:1px solid #222;color:#fff;',
        })
    )
    mensaje = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 5,
            'placeholder': 'Describí tu sugerencia, error o consulta...',
            'style': 'background:#111;border:1px solid #222;color:#fff;',
        })
    )
