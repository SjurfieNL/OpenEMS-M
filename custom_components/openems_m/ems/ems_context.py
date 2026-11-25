# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Bouwt de actuele EMSContext uit managers, helpers en plannen."""

from __future__ import annotations

from .models import EMSContext


class EMSContextBuilder:
    """Maakt één consistente momentopname van het volledige EMS."""

    @staticmethod
    def build(**values) -> EMSContext:
        return EMSContext(**values)
