from __future__ import annotations

from crm.models import Deal, Pipeline, PipelineStage


def ensure_open_deal(contact, brand):
    existing = Deal.objects.filter(
        tenant=contact.tenant,
        contact=contact,
        brand=brand,
        status="open",
    ).select_related("pipeline", "stage").first()
    if existing:
        return existing, False

    pipeline = Pipeline.objects.filter(
        tenant=contact.tenant,
        brand=brand,
        is_default=True,
    ).first() or Pipeline.objects.filter(tenant=contact.tenant, brand=brand).first()
    if not pipeline:
        pipeline = Pipeline.objects.create(
            tenant=contact.tenant,
            brand=brand,
            name=f"Pipeline {brand.name}",
            is_default=True,
        )
    stage = pipeline.stages.order_by("position", "created_at").first()
    if not stage:
        stage = PipelineStage.objects.create(
            tenant=contact.tenant,
            pipeline=pipeline,
            name="Lead Baru",
            position=0,
            probability=5,
        )
    deal = Deal.objects.create(
        tenant=contact.tenant,
        brand=brand,
        contact=contact,
        pipeline=pipeline,
        stage=stage,
        owner=contact.owner,
        title=f"{brand.name} — {contact}",
        value=0,
        status="open",
    )
    return deal, True
