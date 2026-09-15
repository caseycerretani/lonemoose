"""Source connectors."""

from __future__ import annotations

from typing import Dict, Type

from ..httpclient import PoliteClient
from .arcgis import ArcGISConnector
from .base import Connector, HarvestQuery
from .csvfeed import CsvConnector
from .socrata import SocrataConnector

CONNECTORS: Dict[str, Type[Connector]] = {
    SocrataConnector.name: SocrataConnector,
    ArcGISConnector.name: ArcGISConnector,
    CsvConnector.name: CsvConnector,
}


def get_connector(name: str, client: PoliteClient) -> Connector:
    try:
        return CONNECTORS[name](client)
    except KeyError as exc:
        raise ValueError(
            f"unknown connector {name!r}; known: {sorted(CONNECTORS)}"
        ) from exc


__all__ = [
    "Connector",
    "HarvestQuery",
    "SocrataConnector",
    "ArcGISConnector",
    "CsvConnector",
    "CONNECTORS",
    "get_connector",
]
