from __future__ import annotations

import json

from django import forms
from django.contrib.auth.models import User

from .models import (
    AIReview,
    Agent,
    AutomationRule,
    Brand,
    Campaign,
    ChannelConnection,
    Contact,
    KnowledgeEntry,
    Membership,
    PipelineStage,
)
from .services.automations import validate_rule_config
from .services.crypto import decrypt_dict, encrypt_dict


class StyledFormMixin:
    def _style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ContactForm(StyledFormMixin, forms.ModelForm):
    tags_text = forms.CharField(required=False, label="Tags", help_text="Pisahkan dengan koma")

    class Meta:
        model = Contact
        fields = [
            "brand",
            "name",
            "phone",
            "email",
            "company",
            "city",
            "source",
            "status",
            "lead_score",
            "marketing_consent",
            "notes",
        ]

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
        fields = [
            "brand",
            "name",
            "description",
            "system_prompt",
            "greeting",
            "tone",
            "language",
            "mode",
            "confidence_threshold",
            "handoff_keywords",
            "is_active",
        ]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 12}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "greeting": forms.Textarea(attrs={"rows": 4}),
            "handoff_keywords": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        self._style()


class KnowledgeEntryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = KnowledgeEntry
        fields = ["agent", "title", "category", "content", "source_url", "is_active"]
        widgets = {"content": forms.Textarea(attrs={"rows": 14})}

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agent"].queryset = Agent.objects.filter(tenant=tenant)
        self._style()


class ConnectionForm(StyledFormMixin, forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        label="API key / token",
        help_text="Kosongkan saat edit untuk mempertahankan credential yang sudah tersimpan.",
    )
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
                # Do not echo secrets into HTML. A placeholder signals that a key exists.
                self.fields["api_key"].widget.attrs["placeholder"] = "Credential tersimpan — kosongkan untuk mempertahankan"
                self.fields["from_name"].initial = data.get("from_name", "")
                self.fields["from_email"].initial = data.get("from_email", "")
            except Exception:
                pass
        self._style()

    def save(self, commit=True):
        obj = super().save(commit=False)
        existing = {}
        if obj.pk and obj.encrypted_credentials:
            try:
                existing = decrypt_dict(obj.encrypted_credentials)
            except Exception:
                existing = {}
        key_name = "api_token" if obj.provider == "mailketing" else "api_key"
        submitted_key = self.cleaned_data.get("api_key", "").strip()
        credentials = {
            key_name: submitted_key or existing.get(key_name, ""),
            "from_name": self.cleaned_data.get("from_name", "") or existing.get("from_name", ""),
            "from_email": self.cleaned_data.get("from_email", "") or existing.get("from_email", ""),
        }
        obj.encrypted_credentials = encrypt_dict(credentials)
        if commit:
            obj.save()
        return obj


class ReplyForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 3, "class": "form-control", "placeholder": "Ketik balasan..."}
        )
    )


class InternalNoteForm(forms.Form):
    note = forms.CharField(
        label="Catatan internal",
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control", "placeholder": "Catatan hanya untuk tim..."}
        ),
    )


class ConversationAssignmentForm(StyledFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label="Ditangani oleh")

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = User.objects.filter(
            memberships__tenant=tenant,
            memberships__is_active=True,
        ).distinct()
        self._style()


class AgentTestForm(forms.Form):
    question = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 4, "class": "form-control", "placeholder": "Contoh: Berapa biaya pendirian PT?"}
        )
    )


class CampaignForm(StyledFormMixin, forms.ModelForm):
    confirm_consent = forms.BooleanField(
        required=True,
        label="Saya memastikan penerima memiliki consent marketing yang sah",
    )

    class Meta:
        model = Campaign
        fields = [
            "brand",
            "connection",
            "name",
            "channel",
            "subject",
            "content",
            "tag_filter",
            "scheduled_at",
        ]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 10, "placeholder": "Halo {{name}}, ..."}),
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        self.fields["connection"].queryset = ChannelConnection.objects.filter(tenant=tenant, is_active=True)
        self.fields["scheduled_at"].input_formats = ["%Y-%m-%dT%H:%M"]
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


class AutomationRuleForm(StyledFormMixin, forms.ModelForm):
    config_text = forms.CharField(
        label="Konfigurasi JSON",
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text=(
            'Contoh tag: {"tag":"hot-lead"}; tugas: {"title":"Follow up {{name}}","due_minutes":60}; '
            'WhatsApp: {"message":"Halo {{name}}..."}; n8n: {"url":"https://..."}; '
            'no_reply tambahkan "minutes":60.'
        ),
    )

    class Meta:
        model = AutomationRule
        fields = [
            "brand",
            "name",
            "trigger",
            "action",
            "delay_minutes",
            "cooldown_minutes",
            "is_active",
        ]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant)
        if self.instance.pk:
            self.fields["config_text"].initial = json.dumps(
                self.instance.config or {}, ensure_ascii=False, indent=2
            )
        else:
            self.fields["config_text"].initial = "{}"
        self._style()

    def clean_config_text(self):
        raw = self.cleaned_data["config_text"]
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"JSON tidak valid: {exc}") from exc
        if not isinstance(data, dict):
            raise forms.ValidationError("Konfigurasi harus berupa object JSON")
        return data

    def clean(self):
        data = super().clean()
        action = data.get("action")
        config = data.get("config_text")
        if action and config is not None:
            try:
                validate_rule_config(action, config)
            except ValueError as exc:
                self.add_error("config_text", str(exc))
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.config = self.cleaned_data["config_text"]
        if commit:
            obj.save()
        return obj


class DealStageForm(StyledFormMixin, forms.Form):
    stage = forms.ModelChoiceField(queryset=PipelineStage.objects.none(), label="Tahap")

    def __init__(self, *args, deal=None, **kwargs):
        super().__init__(*args, **kwargs)
        if deal:
            self.fields["stage"].queryset = deal.pipeline.stages.all()
            self.fields["stage"].initial = deal.stage
        self._style()


class AIReviewForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AIReview
        fields = ["verdict", "comment", "corrected_response"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 3}),
            "corrected_response": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class MemberInviteForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email pengguna")
    role = forms.ChoiceField(choices=Membership.ROLE_CHOICES)
    temporary_password = forms.CharField(
        min_length=12,
        widget=forms.PasswordInput,
        label="Password sementara",
        help_text="Kirimkan melalui kanal aman dan minta pengguna menggantinya setelah login.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
