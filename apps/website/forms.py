from django import forms

from .models import ContactInquiry


class ContactInquiryForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = ContactInquiry
        fields = ["name", "email", "phone", "service", "message"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Tu nombre"}),
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "tu@correo.com"}
            ),
            "phone": forms.TextInput(
                attrs={"autocomplete": "tel", "placeholder": "+502 0000 0000"}
            ),
            "message": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Cuéntanos sobre el proyecto, la fecha y el lugar.",
                }
            ),
        }

    def clean_website(self):
        value = self.cleaned_data.get("website", "")
        if value:
            raise forms.ValidationError("No fue posible enviar el mensaje.")
        return value
