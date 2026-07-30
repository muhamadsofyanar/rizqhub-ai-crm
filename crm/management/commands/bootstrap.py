import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from crm.models import Agent, Brand, KnowledgeEntry, Membership, Pipeline, PipelineStage, Tenant


AGENT_DATA = [
    {
        "brand": "Jasa Legalitas",
        "slug": "legalitas",
        "agent": "Legal Assistant",
        "description": "Mengkualifikasi calon klien dan menjelaskan layanan legalitas berdasarkan data resmi.",
        "prompt": "Kumpulkan jenis badan usaha, bidang usaha, domisili, jumlah pendiri, target waktu, dan kebutuhan tambahan. Jangan menjamin izin terbit dan jangan memberikan keputusan hukum final. Untuk KBLI, dokumen sensitif, komplain, refund, dan negosiasi, alihkan kepada staf legal.",
        "stages": ["Lead Baru", "Analisis Kebutuhan", "Qualified", "Konsultasi", "Penawaran", "Menunggu Pembayaran", "Pengumpulan Dokumen", "Proses", "Selesai"],
        "knowledge": [
            ("Batasan layanan AI legalitas", "AI membantu informasi awal, kualifikasi kebutuhan, dan penjadwalan. Keputusan hukum, validasi dokumen, pemilihan KBLI final, dan jaminan penerbitan izin wajib dikonfirmasi oleh staf legal."),
            ("Data awal calon klien", "Data awal yang perlu dikumpulkan: bentuk usaha yang diinginkan, bidang usaha utama, lokasi atau domisili, jumlah pendiri, status tempat usaha, target waktu, serta layanan tambahan yang dibutuhkan."),
        ],
    },
    {
        "brand": "STIFIn",
        "slug": "stifin",
        "agent": "STIFIn Assistant",
        "description": "Menjawab informasi layanan, jadwal, lokasi, dan pendaftaran berdasarkan materi resmi.",
        "prompt": "Jelaskan layanan hanya dari materi resmi. Kumpulkan nama, kota, jumlah peserta, dan jadwal pilihan. Jangan memberikan diagnosis medis atau psikologis dan jangan membuat klaim hasil di luar sumber resmi.",
        "stages": ["Pertanyaan Baru", "Berminat", "Pilih Jadwal", "Terdaftar", "Menunggu Pembayaran", "Lunas", "Tes Selesai", "Follow-up"],
        "knowledge": [
            ("Batasan layanan AI STIFIn", "AI hanya menjelaskan informasi layanan dan administrasi berdasarkan materi resmi. Interpretasi individual, klaim psikologis, dan pertanyaan sensitif harus dialihkan kepada promotor atau fasilitator manusia."),
            ("Data pendaftaran", "Data minimum pendaftaran meliputi nama peserta, usia, kota, nomor WhatsApp, jumlah peserta, jadwal pilihan, dan metode pembayaran."),
        ],
    },
    {
        "brand": "Produk Digital",
        "slug": "produk-digital",
        "agent": "Digital Product Assistant",
        "description": "Menjual produk digital dan memberikan dukungan akses dasar.",
        "prompt": "Bantu pelanggan memilih produk, menjelaskan fitur dan harga yang tersedia pada sumber, serta menangani kendala akses dasar. Jangan mengirim akses sebelum pembayaran terverifikasi. Refund, lisensi khusus, dan masalah pembayaran harus dialihkan kepada admin.",
        "stages": ["Visitor", "Minat Produk", "Checkout", "Menunggu Pembayaran", "Lunas", "Akses Dikirim", "Support", "Upsell"],
        "knowledge": [
            ("Aturan akses produk digital", "Akses produk hanya dikirim setelah pembayaran berstatus terverifikasi. Kendala link, lisensi, refund, atau pembayaran ganda harus diperiksa oleh admin."),
            ("Data pesanan", "Data minimum pesanan meliputi nama, email aktif, nomor WhatsApp, produk yang dipilih, dan bukti atau status pembayaran."),
        ],
    },
]


class Command(BaseCommand):
    help = "Create initial admin, workspace, brands, agents, pipelines and starter knowledge"

    def handle(self, *args, **options):
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_PASSWORD")
        user, created = User.objects.get_or_create(username=email, defaults={"email": email, "is_staff": True, "is_superuser": True})
        if created:
            if not password:
                self.stdout.write(self.style.WARNING("ADMIN_PASSWORD kosong; admin dibuat tanpa password yang dapat digunakan."))
                user.set_unusable_password()
            else:
                user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
        elif password and os.getenv("RESET_ADMIN_PASSWORD", "false").lower() == "true":
            user.set_password(password)
            user.save(update_fields=["password"])

        tenant, _ = Tenant.objects.get_or_create(slug="rizqhub", defaults={"name": "RizqHub"})
        Membership.objects.get_or_create(tenant=tenant, user=user, defaults={"role": "owner"})

        for item in AGENT_DATA:
            brand, _ = Brand.objects.get_or_create(tenant=tenant, slug=item["slug"], defaults={"name": item["brand"], "description": item["description"]})
            agent, _ = Agent.objects.get_or_create(
                tenant=tenant,
                brand=brand,
                name=item["agent"],
                defaults={
                    "description": item["description"],
                    "system_prompt": item["prompt"],
                    "mode": "draft",
                    "is_active": True,
                },
            )
            pipeline, _ = Pipeline.objects.get_or_create(tenant=tenant, brand=brand, name=f"Pipeline {brand.name}", defaults={"is_default": True})
            for position, stage_name in enumerate(item["stages"]):
                PipelineStage.objects.get_or_create(
                    tenant=tenant,
                    pipeline=pipeline,
                    name=stage_name,
                    defaults={"position": position, "probability": min(position * 10, 90)},
                )
            for title, content in item["knowledge"]:
                KnowledgeEntry.objects.get_or_create(tenant=tenant, agent=agent, title=title, defaults={"content": content, "category": "Starter"})

        self.stdout.write(self.style.SUCCESS(f"Bootstrap selesai. Login: {email}"))
