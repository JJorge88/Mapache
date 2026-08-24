from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            if not data:
                raise forms.ValidationError("Selecciona al menos una fotografía.")
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class MultiplePhotoUploadForm(forms.Form):
    photos = MultipleFileField(
        label="Fotografías",
        widget=MultipleFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp"}),
    )
