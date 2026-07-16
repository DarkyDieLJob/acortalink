from django import forms
from django.contrib.auth.forms import UserCreationForm


class ContactoAcortalinkForm(forms.Form):
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
    mensaje = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Quiero activar premium...',
            'style': 'background:#111;border:1px solid #222;color:#fff;',
        })
    )
