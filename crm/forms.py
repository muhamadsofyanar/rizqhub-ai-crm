from django import forms
from .models import Agent, Brand, ChannelConnection, Contact, Deal, KnowledgeEntry, Task
from .services.crypto import decrypt_dict, encrypt_dict


class StyledFormMixin:
    def _style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ContactForm(StyledFormMixin, forms.ModelForm):
    tags_text = forms.CharField(required=False, label="Tags", help_text="Pisahkan dengan koma")

    class Meta:
        model = Contact
        fields = ["brand", "name", "phone", "email", "company", "city", "source", "status", "lead_score", "marketing_consent", "notes"]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        if self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(self.instance.tags or [])
        self._style()

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.tags = [x.strip() for x in self.cleaned_data.get("tags_text", "").split(",") if x.strip()]
        if commit:
            obj.save()
        return obj


class AgentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Agent
        fields = ["brand", "name", "description", "system_prompt", "greeting", "tone", "language", "mode", "confidence_threshold", "handoff_keywords", "is_active"]
        widgets = {"system_prompt": forms.Textarea(attrs={"rows": 10}), "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        self._style()


class KnowledgeEntryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = KnowledgeEntry
        fields = ["agent", "title", "category", "content", "source_url", "is_active"]
        widgets = {"content": forms.Textarea(attrs={"rows": 12})}

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agent"].queryset = Agent.objects.filter(tenant=tenant)
        self._style()


class ConnectionForm(StyledFormMixin, forms.ModelForm):
    api_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True), label="API key / token")
    from_name = forms.CharField(required=False, label="Nama pengirim email")
    from_email = forms.EmailField(required=False, label="Email pengirim terverifikasi")

    class Meta:
        model = ChannelConnection
        fields = ["brand", "agent", "provider", "name", "external_id", "is_active"]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        self.fields["agent"].queryset = Agent.objects.filter(tenant=tenant)
        if self.instance.pk and self.instance.encrypted_credentials:
            try:
                data = decrypt_dict(self.instance.encrypted_credentials)
                self.fields["api_key"].initial = data.get("api_key") or data.get("api_token") or ""
                self.fields["from_name"].initial = data.get("from_name", "")
                self.fields["from_email"].initial = data.get("from_email", "")
            except Exception:
                pass
        self._style()

    def save(self, commit=True):
        obj = super().save(commit=False)
        key_name = "api_token" if obj.provider == "mailketing" else "api_key"
        obj.encrypted_credentials = encrypt_dict({
            key_name: self.cleaned_data.get("api_key", ""),
            "from_name": self.cleaned_data.get("from_name", ""),
            "from_email": self.cleaned_data.get("from_email", ""),
        })
        if commit:
            obj.save()
        return obj


class ReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Ketik balasan..."}))


class AgentTestForm(forms.Form):
    question = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "class": "form-control", "placeholder": "Contoh: Berapa biaya pendirian PT?"}))

class CampaignForm(StyledFormMixin, forms.ModelForm):
    confirm_consent = forms.BooleanField(required=True, label="Saya memastikan penerima memiliki consent marketing yang sah")

    class Meta:
        from .models import Campaign
        model = Campaign
        fields = ["brand", "connection", "name", "channel", "subject", "content", "tag_filter"]
        widgets = {"content": forms.Textarea(attrs={"rows": 10, "placeholder": "Halo {{name}}, ..."})}

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        self.fields["connection"].queryset = ChannelConnection.objects.filter(tenant=tenant, is_active=True)
        self._style()

    def clean(self):
        data = super().clean()
        connection = data.get("connection")
        channel = data.get("channel")
        if connection and channel:
            expected = "starsender" if channel == "whatsapp" else "mailketing"
            if connection.provider != expected:
                self.add_error("connection", f"Channel {channel} memerlukan provider {expected}.")
        if channel == "email" and not data.get("subject"):
            self.add_error("subject", "Subject wajib untuk campaign email.")
        return data
