from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relayforge.models import Delivery, DeliveryAttempt, DeliveryState


def render_metrics(session_factory: sessionmaker[Session]) -> str:
    lines = [
        "# HELP relayforge_deliveries Current number of deliveries by state.",
        "# TYPE relayforge_deliveries gauge",
    ]
    with session_factory() as session:
        counts = dict(
            session.execute(
                select(Delivery.state, func.count(Delivery.id)).group_by(Delivery.state)
            ).all()
        )
        attempts = session.scalar(select(func.count(DeliveryAttempt.id))) or 0
    for state in DeliveryState:
        lines.append(f'relayforge_deliveries{{state="{state.value}"}} {counts.get(state, 0)}')
    lines.extend(
        [
            "# HELP relayforge_delivery_attempts_total Total persisted delivery attempts.",
            "# TYPE relayforge_delivery_attempts_total counter",
            f"relayforge_delivery_attempts_total {attempts}",
        ]
    )
    return "\n".join(lines) + "\n"
