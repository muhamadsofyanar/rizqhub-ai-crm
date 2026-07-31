from __future__ import annotations

import json

from django import forms
from django.contrib.auth.models import User

from .models import (
    AIReview,
    Agent,
    AgentRuntimePolicy,
    AutomationRule,
    Broadcast,
    BroadcastTemplate,
    Brand,
    Campaign,
    Contact,
    Deal,
    GroupCategory,
    GroupCategoryMembership,
    GroupPreset,
    GroupPresetMember,
    ChannelConnection,
    KnowledgeEntry,
    Membership,
    PipelineStage,
    StarSenderAccount,
    StarSenderDevice,
    WhatsAppGroup,
)
from .services.automations import validate_rule_config
from .services.crypto import decrypt_dict, encrypt_dict
from .services.starsender import ensure_device_connection


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
        self.fields["connection"].queryset = ChannelConnection.objects.filter(
            tenant=tenant, is_active=True, provider="mailketing"
        )
        self.fields["connection"].label = "Koneksi Mailketing"
        self.fields["channel"].initial = "email"
        self.fields["channel"].widget = forms.HiddenInput()
        self.fields["name"].label = "Nama campaign email"
        self.fields["subject"].label = "Subjek email"
        self.fields["content"].label = "Isi email"
        self.fields["tag_filter"].label = "Filter tag kontak (opsional)"
        self.fields["scheduled_at"].label = "Jadwal kirim (opsional)"
        self.fields["scheduled_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self._style()

    def clean(self):
        data = super().clean()
        data["channel"] = "email"
        connection = data.get("connection")
        if connection and connection.provider != "mailketing":
            self.add_error("connection", "Campaign email wajib memakai koneksi Mailketing.")
        if not data.get("subject"):
            self.add_error("subject", "Subjek wajib untuk campaign email.")
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


class DealQuickUpdateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Deal
        fields = ["value", "status", "owner", "expected_close_date", "lost_reason"]
        widgets = {
            "expected_close_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "value": "Nilai potensi",
            "status": "Hasil deal",
            "owner": "Penanggung jawab",
            "expected_close_date": "Target selesai",
            "lost_reason": "Alasan tidak lanjut",
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        user_ids = Membership.objects.filter(
            tenant=tenant, is_active=True
        ).values_list("user_id", flat=True)
        self.fields["owner"].queryset = User.objects.filter(id__in=user_ids).order_by(
            "first_name", "username"
        )
        self.fields["owner"].required = False
        self.fields["lost_reason"].required = False
        self._style()

    def clean(self):
        data = super().clean()
        if data.get("status") == "lost" and not (data.get("lost_reason") or "").strip():
            self.add_error("lost_reason", "Isi alasan ketika deal ditandai tidak lanjut.")
        if data.get("status") != "lost":
            data["lost_reason"] = ""
        return data


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


class AgentRuntimePolicyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AgentRuntimePolicy
        fields = [
            "clarification_threshold",
            "reply_threshold",
            "auto_handoff_low_confidence",
            "max_reply_chars",
            "max_questions",
            "reply_delay_seconds",
            "safe_clarification_text",
            "hard_handoff_keywords",
            "is_active",
        ]
        widgets = {
            "safe_clarification_text": forms.Textarea(attrs={"rows": 4}),
            "hard_handoff_keywords": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean(self):
        data = super().clean()
        clarify = data.get("clarification_threshold")
        reply = data.get("reply_threshold")
        if clarify is not None and reply is not None and clarify >= reply:
            self.add_error(
                "reply_threshold",
                "Reply threshold harus lebih tinggi daripada clarification threshold.",
            )
        return data


class StarSenderAccountForm(StyledFormMixin, forms.ModelForm):
    account_api_key = forms.CharField(
        required=False,
        label="Account API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Kosongkan saat edit untuk mempertahankan key yang sudah tersimpan.",
    )

    class Meta:
        model = StarSenderAccount
        fields = ["name", "is_active", "auto_sync_devices"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Nama akun"
        self.fields["is_active"].label = "Akun aktif"
        self.fields["auto_sync_devices"].label = "Sinkronkan device otomatis"
        if self.instance.pk and self.instance.encrypted_account_key:
            self.fields["account_api_key"].widget.attrs["placeholder"] = (
                "Credential tersimpan — kosongkan untuk mempertahankan"
            )
        self._style()

    def clean_account_api_key(self):
        submitted = (self.cleaned_data.get("account_api_key") or "").strip()
        if not self.instance.pk and not submitted:
            raise forms.ValidationError("Account API Key wajib saat membuat akun StarSender.")
        return submitted

    def save(self, commit=True):
        obj = super().save(commit=False)
        existing = {}
        if obj.pk and obj.encrypted_account_key:
            try:
                existing = decrypt_dict(obj.encrypted_account_key)
            except Exception:
                existing = {}
        submitted = self.cleaned_data.get("account_api_key", "").strip()
        key = submitted or existing.get("account_api_key", "")
        obj.encrypted_account_key = encrypt_dict({"account_api_key": key}) if key else ""
        if commit:
            obj.save()
        return obj


class StarSenderDeviceForm(StyledFormMixin, forms.ModelForm):
    device_key = forms.CharField(
        required=False,
        label="Device Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Wajib untuk mengirim pesan dan sinkronisasi grup. Kosongkan saat edit untuk mempertahankan.",
    )

    class Meta:
        model = StarSenderDevice
        fields = [
            "name",
            "phone_number",
            "brand",
            "agent",
            "send_enabled",
            "group_sync_enabled",
            "is_default",
            "is_fallback",
        ]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["name"].label = "Nama device"
        self.fields["phone_number"].label = "Nomor WhatsApp"
        self.fields["brand"].label = "Brand / Bisnis"
        self.fields["agent"].label = "AI Agent"
        self.fields["send_enabled"].label = "Aktifkan pengiriman"
        self.fields["group_sync_enabled"].label = "Aktifkan sinkronisasi grup"
        self.fields["is_default"].label = "Device utama"
        self.fields["is_fallback"].label = "Device cadangan"
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant, is_active=True)
        self.fields["agent"].queryset = Agent.objects.filter(tenant=tenant, is_active=True)
        if self.instance.pk and self.instance.encrypted_device_key:
            self.fields["device_key"].widget.attrs["placeholder"] = (
                "Credential tersimpan — kosongkan untuk mempertahankan"
            )
        self._style()

    def clean(self):
        data = super().clean()
        brand = data.get("brand")
        agent = data.get("agent")
        if brand and agent and agent.brand_id != brand.id:
            self.add_error("agent", "Agent harus berasal dari brand yang dipilih.")
        submitted = (self.cleaned_data.get("device_key") or "").strip()
        existing_key = ""
        if self.instance.pk and self.instance.encrypted_device_key:
            try:
                existing_key = decrypt_dict(self.instance.encrypted_device_key).get("device_key", "")
            except Exception:
                existing_key = ""
        has_existing = bool(existing_key)
        if (data.get("send_enabled") or data.get("group_sync_enabled")) and not submitted and not has_existing:
            self.add_error(
                "device_key",
                "Device Key wajib untuk pengiriman atau sinkronisasi grup.",
            )
        if data.get("send_enabled") and (not brand or not agent):
            self.add_error("send_enabled", "Pilih Brand dan Agent sebelum mengaktifkan pengiriman.")
        if data.get("is_default") and data.get("is_fallback"):
            self.add_error("is_fallback", "Satu device tidak boleh sekaligus menjadi default dan fallback.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        existing = {}
        if obj.pk and obj.encrypted_device_key:
            try:
                existing = decrypt_dict(obj.encrypted_device_key)
            except Exception:
                existing = {}
        submitted = self.cleaned_data.get("device_key", "").strip()
        key = submitted or existing.get("device_key", "")
        obj.encrypted_device_key = encrypt_dict({"device_key": key}) if key else ""
        if commit:
            obj.save()
            if obj.brand_id and obj.is_default:
                StarSenderDevice.objects.filter(
                    tenant=obj.tenant,
                    brand=obj.brand,
                    is_default=True,
                ).exclude(pk=obj.pk).update(is_default=False)
            if obj.brand_id and obj.is_fallback:
                StarSenderDevice.objects.filter(
                    tenant=obj.tenant,
                    brand=obj.brand,
                    is_fallback=True,
                ).exclude(pk=obj.pk).update(is_fallback=False)
            ensure_device_connection(obj)
        return obj


class WhatsAppGroupForm(StyledFormMixin, forms.ModelForm):
    categories = forms.ModelMultipleChoiceField(
        queryset=GroupCategory.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}),
        label="Kategori",
    )

    class Meta:
        model = WhatsAppGroup
        fields = ["name", "is_active", "is_locked", "ai_mode"]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["categories"].queryset = GroupCategory.objects.filter(
            tenant=tenant, is_active=True
        )
        if self.instance.pk:
            self.fields["categories"].initial = [
                link.category_id for link in self.instance.category_links.all()
            ]
        self._style()

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit:
            selected = set(self.cleaned_data.get("categories", []).values_list("id", flat=True))
            obj.category_links.exclude(category_id__in=selected).delete()
            for category in self.cleaned_data.get("categories", []):
                GroupCategoryMembership.objects.get_or_create(
                    tenant=obj.tenant,
                    group=obj,
                    category=category,
                )
        return obj


class GroupCategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = GroupCategory
        fields = ["name", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class GroupPresetForm(StyledFormMixin, forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=WhatsAppGroup.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Grup preset statis",
    )

    class Meta:
        model = GroupPreset
        fields = ["name", "description", "preset_type", "brand", "category", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["brand"].queryset = Brand.objects.filter(tenant=tenant, is_active=True)
        self.fields["category"].queryset = GroupCategory.objects.filter(
            tenant=tenant, is_active=True
        )
        self.fields["groups"].queryset = WhatsAppGroup.objects.filter(
            tenant=tenant, is_active=True, is_locked=False
        ).select_related("device")
        if self.instance.pk:
            self.fields["groups"].initial = [
                member.group_id for member in self.instance.members.all()
            ]
        self._style()

    def clean(self):
        data = super().clean()
        if data.get("preset_type") == "dynamic" and not data.get("category"):
            self.add_error("category", "Preset dinamis wajib memilih kategori.")
        if data.get("preset_type") == "static" and not data.get("groups"):
            self.add_error("groups", "Preset statis wajib memilih minimal satu grup.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit:
            selected = self.cleaned_data.get("groups", [])
            if obj.preset_type == "static":
                selected_ids = set(selected.values_list("id", flat=True))
                obj.members.exclude(group_id__in=selected_ids).delete()
                for group in selected:
                    GroupPresetMember.objects.get_or_create(
                        tenant=obj.tenant,
                        preset=obj,
                        group=group,
                    )
            else:
                obj.members.all().delete()
        return obj


class BroadcastTemplateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = BroadcastTemplate
        fields = ["name", "category", "message_type", "body", "media_file", "media_url", "is_active"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 10, "placeholder": "Tulis isi pesan..."}),
        }
        labels = {
            "name": "Nama template",
            "category": "Kategori",
            "message_type": "Jenis template",
            "body": "Isi pesan",
            "media_file": "Upload media",
            "media_url": "Atau URL media",
            "is_active": "Template aktif",
        }
        help_texts = {
            "category": "Contoh: Promo legalitas, Edukasi, Event, Follow-up.",
            "body": "Boleh menggunakan {{name}}, {{phone}}, {{email}}, dan {{company}} untuk personalisasi kontak.",
            "media_file": "File disimpan di volume media dan dibuka melalui URL token acak untuk StarSender.",
            "media_url": "Opsional bila media sudah tersedia pada URL HTTPS publik.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean(self):
        data = super().clean()
        message_type = data.get("message_type")
        body = (data.get("body") or "").strip()
        media_file = data.get("media_file") or getattr(self.instance, "media_file", None)
        media_url = (data.get("media_url") or "").strip()
        if not body:
            self.add_error("body", "Isi teks wajib. Untuk template media, teks akan dikirim sebagai caption bersama media.")
        uploaded = data.get("media_file")
        if uploaded and getattr(uploaded, "size", 0) > 25 * 1024 * 1024:
            self.add_error("media_file", "Ukuran media maksimal 25 MB untuk menjaga penyimpanan dan pengiriman stabil.")
        if media_url and not media_url.lower().startswith("https://"):
            self.add_error("media_url", "URL media harus menggunakan HTTPS.")
        if message_type == "media" and not media_file and not media_url:
            self.add_error("media_file", "Upload media atau isi URL media.")
        return data


class BroadcastForm(StyledFormMixin, forms.ModelForm):
    template = forms.ModelChoiceField(
        queryset=BroadcastTemplate.objects.none(),
        required=False,
        label="Template pesan",
        help_text="Opsional. Pilih template lalu isi masih dapat diedit sebelum disimpan.",
    )
    message_type = forms.ChoiceField(choices=[("text", "Teks saja"), ("media", "Teks + media")])
    confirm_consent = forms.BooleanField(
        required=False,
        label="Saya memastikan penerima personal memiliki consent yang sah",
    )
    confirm_group_permission = forms.BooleanField(
        required=False,
        label="Saya memiliki izin untuk mengirim ke grup yang dipilih",
    )
    contacts = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 12}),
        label="Kontak personal",
    )
    manual_numbers = forms.CharField(
        required=False,
        label="Nomor manual",
        help_text="Satu nomor per baris. Gunakan hanya nomor yang memiliki consent.",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "62812...\n62813..."}),
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=WhatsAppGroup.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 14, "class": "form-control js-native-groups"}),
        label="Grup tujuan",
    )

    class Meta:
        model = Broadcast
        fields = [
            "name",
            "target_type",
            "device",
            "preset",
            "message_type",
            "body",
            "file_url",
            "delay_seconds",
            "scheduled_at",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 10}),
            "scheduled_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["name"].label = "Nama broadcast"
        self.fields["target_type"].label = "Jenis tujuan"
        self.fields["device"].label = "Device WhatsApp"
        self.fields["preset"].label = "Preset grup"
        self.fields["body"].label = "Isi pesan"
        self.fields["file_url"].label = "URL media"
        self.fields["delay_seconds"].label = "Delay antar tujuan (detik)"
        self.fields["scheduled_at"].label = "Jadwal pengiriman"
        self.fields["device"].queryset = StarSenderDevice.objects.filter(
            tenant=tenant, send_enabled=True
        ).select_related("account")
        self.fields["preset"].queryset = GroupPreset.objects.filter(
            tenant=tenant, is_active=True
        )
        self.fields["template"].queryset = BroadcastTemplate.objects.filter(
            tenant=tenant, is_active=True
        )
        self.fields["contacts"].queryset = Contact.objects.filter(
            tenant=tenant,
            marketing_consent=True,
        ).exclude(phone="")
        self.fields["groups"].queryset = WhatsAppGroup.objects.filter(
            tenant=tenant, is_active=True, is_locked=False
        ).select_related("device")
        self.fields["scheduled_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self._style()

    def clean(self):
        data = super().clean()
        target_type = data.get("target_type")
        device = data.get("device")
        preset = data.get("preset")
        contacts = data.get("contacts")
        groups = data.get("groups")
        manual = [line.strip() for line in (data.get("manual_numbers") or "").splitlines() if line.strip()]
        normalized_manual = []
        invalid_manual = []
        for raw in manual:
            number = "".join(ch for ch in raw if ch.isdigit())
            if number.startswith("0"):
                number = "62" + number[1:]
            if not (8 <= len(number) <= 16):
                invalid_manual.append(raw)
            elif number not in normalized_manual:
                normalized_manual.append(number)
        if invalid_manual:
            self.add_error(
                "manual_numbers",
                "Nomor tidak valid: " + ", ".join(invalid_manual[:5]),
            )
        if len(normalized_manual) > 500:
            self.add_error("manual_numbers", "Maksimal 500 nomor manual per broadcast.")
        data["manual_numbers"] = "\n".join(normalized_manual)
        if target_type == "personal" and not data.get("confirm_consent"):
            self.add_error("confirm_consent", "Konfirmasi consent wajib untuk pengiriman personal.")
        if target_type == "group" and not data.get("confirm_group_permission"):
            self.add_error("confirm_group_permission", "Konfirmasi izin grup wajib sebelum membuat broadcast.")
        if target_type == "personal" and not contacts and not manual:
            self.add_error("contacts", "Pilih kontak atau isi nomor manual.")
        if target_type == "group" and not groups and not preset:
            self.add_error("groups", "Pilih grup atau preset.")
        if target_type == "group" and device:
            invalid_groups = [group for group in (groups or []) if group.device_id != device.id]
            if invalid_groups:
                self.add_error(
                    "groups",
                    "Semua grup harus berasal dari device yang sama dengan device pengiriman.",
                )
        if target_type == "personal" and preset:
            self.add_error("preset", "Preset grup hanya dapat dipakai untuk target grup.")
        delay = data.get("delay_seconds") or 0
        if delay < 30:
            self.add_error("delay_seconds", "Delay minimal 30 detik untuk pengiriman massal yang lebih aman.")
        template = data.get("template")
        template_has_media = bool(
            template and (getattr(template, "media_file", None) or getattr(template, "media_url", ""))
        )
        if data.get("message_type") == "media" and not data.get("file_url") and not template_has_media:
            self.add_error("file_url", "Pilih template media, upload media pada template, atau isi URL file.")
        template_has_body = bool(template and (getattr(template, "body", "") or "").strip())
        if not (data.get("body") or "").strip() and not template_has_body:
            self.add_error("body", "Isi teks wajib. Pada mode media, teks dikirim sebagai caption bersama file.")
        return data
