from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.media_processing.services import validate_image_file


class CameraCompatibleFileInput(forms.ClearableFileInput):
    def use_required_attribute(self, initial):
        return False


class GalleryAISettingsForm(forms.Form):
    enabled = forms.BooleanField(required=False)
    face_search_enabled = forms.BooleanField(required=False)
    bib_search_enabled = forms.BooleanField(required=False)
    bib_format = forms.ChoiceField(
        choices=(("NUMERIC", "Numérico"), ("ALPHANUMERIC", "Alfanumérico")),
        required=False,
    )
    bib_min_length = forms.IntegerField(min_value=1, max_value=16, initial=1, required=False)
    bib_max_length = forms.IntegerField(min_value=1, max_value=16, initial=6, required=False)

    def clean(self):
        cleaned = super().clean()
        cleaned["bib_format"] = cleaned.get("bib_format") or "NUMERIC"
        cleaned["bib_min_length"] = cleaned.get("bib_min_length") or 1
        cleaned["bib_max_length"] = cleaned.get("bib_max_length") or 6
        if cleaned.get("face_search_enabled") and not cleaned.get("enabled"):
            self.add_error("face_search_enabled", "Activa Mapache AI para habilitar esta opción.")
        if cleaned.get("bib_search_enabled") and not cleaned.get("enabled"):
            self.add_error("bib_search_enabled", "Activa Mapache AI para habilitar esta opción.")
        if (
            cleaned.get("bib_min_length") is not None
            and cleaned.get("bib_max_length") is not None
            and cleaned["bib_min_length"] > cleaned["bib_max_length"]
        ):
            self.add_error("bib_max_length", "Debe ser igual o mayor que la longitud mínima.")
        return cleaned


class BibSearchForm(forms.Form):
    query_number = forms.CharField(
        label="Número de dorsal",
        max_length=64,
        widget=forms.TextInput(attrs={"placeholder": "247", "autocomplete": "off"}),
    )


class FaceSearchForm(forms.Form):
    query_image = forms.ImageField(
        label="Fotografía de referencia",
        error_messages={"required": "Selecciona una fotografía donde podamos verte."},
        widget=CameraCompatibleFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "data-library-input": "",
            }
        ),
    )
    consent = forms.BooleanField(
        required=True,
        error_messages={"required": "Necesitamos tu consentimiento para realizar la búsqueda."},
    )

    def clean_query_image(self):
        uploaded_file = self.cleaned_data["query_image"]
        max_bytes = settings.MAPACHE_FACE_QUERY_MAX_MB * 1024 * 1024
        if uploaded_file.size > max_bytes:
            raise ValidationError(
                f"La imagen supera el límite de {settings.MAPACHE_FACE_QUERY_MAX_MB} MB."
            )
        validate_image_file(uploaded_file)
        return uploaded_file


class CombinedSearchForm(FaceSearchForm):
    query_number = forms.CharField(
        label="Número de dorsal",
        max_length=64,
        widget=forms.TextInput(attrs={"placeholder": "247", "autocomplete": "off"}),
    )
