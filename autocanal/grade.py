"""Grade semanal de publicação: slots em horário local convertidos para UTC."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from autocanal.config import Canal

DIAS = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "dom": 6}


def slots(canal: Canal, formato: str, inicio: datetime, fim: datetime, fuso: str) -> list[datetime]:
    """Slots da grade em UTC, ordenados, em [inicio, fim)."""
    tz = ZoneInfo(fuso)
    dia = inicio.astimezone(tz).date() - timedelta(days=1)
    ultimo = fim.astimezone(tz).date() + timedelta(days=1)
    achados: set[datetime] = set()
    while dia <= ultimo:
        for s in canal.grade:
            if s.formato != formato or dia.weekday() not in {DIAS[d] for d in s.dias}:
                continue
            h, m = (int(p) for p in s.hora.split(":"))
            # data+hora locais construídas por dia: seguro em mudanças de horário de verão
            local = datetime.combine(dia, time(h, m), tzinfo=tz)
            achados.add(local.astimezone(UTC))
        dia += timedelta(days=1)
    return sorted(x for x in achados if inicio <= x < fim)


def proximo_slot(
    canal: Canal, formato: str, depois_de: datetime, ocupados: set[datetime], fuso: str
) -> datetime:
    """Primeiro slot >= depois_de que não esteja em `ocupados` (até 60 dias)."""
    for s in slots(canal, formato, depois_de, depois_de + timedelta(days=60), fuso):
        if s not in ocupados:
            return s
    raise ValueError(f"sem slot livre para {canal.slug}/{formato} nos próximos 60 dias")
