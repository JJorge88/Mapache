from django import forms

from .models import Gallery


class DateInput(forms.DateInput):
    input_type = "date"


class GalleryCreateForm(forms.ModelForm):
    pin = forms.CharField(
        required=False,
        min_length=4,
        max_length=8,
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "new-password"}),
        help_text="Solo para galerías privadas: entre 4 y 8 dígitos.",
    )
    enable_mapache_ai = forms.BooleanField(
        label="Activar Mapache AI en este evento",
        required=False,
        help_text="Permite buscar fotografías por rostro y número dentro de la galería.",
    )

    class Meta:
        model = Gallery
        fields = (
            "title",
            "event_date",
            "description",
            "visibility",
            "allow_photo_download",
            "allow_gallery_download",
            "show_in_portfolio",
            "is_featured",
        )
        widgets = {"event_date": DateInput(), "description": forms.Textarea(attrs={"rows": 5})}
        labels = {
            "title": "Nombre de la galería",
            "event_date": "Fecha del evento",
            "description": "Descripción",
            "visibility": "Visibilidad",
            "allow_photo_download": "Permitir descarga individual",
            "allow_gallery_download": "Permitir descarga completa",
            "show_in_portfolio": "Mostrar en portafolio",
            "is_featured": "Destacar en inicio",
        }

    def clean_pin(self) -> str:
        pin = self.cleaned_data.get("pin", "")
        if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
            raise forms.ValidationError("El PIN debe contener entre 4 y 8 dígitos.")
        return pin


class GalleryEditForm(forms.ModelForm):
    class Meta:
        model = Gallery
        fields = (
            "title",
            "event_date",
            "description",
            "allow_photo_download",
            "allow_gallery_download",
            "show_in_portfolio",
            "is_featured",
        )
        widgets = {"event_date": DateInput(), "description": forms.Textarea(attrs={"rows": 5})}
        labels = {
            "title": "Nombre de la galería",
            "event_date": "Fecha del evento",
            "description": "Descripción",
            "allow_photo_download": "Permitir descarga individual",
            "allow_gallery_download": "Permitir descarga completa",
            "show_in_portfolio": "Mostrar en portafolio",
            "is_featured": "Destacar en inicio",
        }


class GalleryAccessForm(forms.Form):
    visibility = forms.ChoiceField(label="Visibilidad", choices=Gallery.Visibility.choices)
    allow_photo_download = forms.BooleanField(label="Permitir descarga individual", required=False)
    allow_gallery_download = forms.BooleanField(label="Permitir descarga completa", required=False)
    pin = forms.CharField(
        required=False,
        min_length=4,
        max_length=8,
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "new-password"}),
        help_text="Déjalo vacío para conservar el PIN configurado.",
    )

    def __init__(self, *args, instance: Gallery, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.update(
                {
                    "visibility": instance.visibility,
                    "allow_photo_download": instance.allow_photo_download,
                    "allow_gallery_download": instance.allow_gallery_download,
                }
            )

    def clean_pin(self) -> str:
        pin = self.cleaned_data.get("pin", "")
        if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
            raise forms.ValidationError("El PIN debe contener entre 4 y 8 dígitos.")
        return pin

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("visibility") == Gallery.Visibility.PRIVATE_PIN
            and self.instance.status == Gallery.Status.PUBLISHED
            and not self.instance.has_pin
            and not cleaned.get("pin")
        ):
            self.add_error("pin", "Configura un PIN para proteger esta galería publicada.")
        return cleaned


class GalleryPinAccessForm(forms.Form):
    pin = forms.CharField(
        label="PIN",
        min_length=4,
        max_length=8,
        widget=forms.PasswordInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "••••",
            }
        ),
    )
