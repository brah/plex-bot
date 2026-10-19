"""plex.tv's public status page (status.plex.tv, hosted on incident.io).

Two public feeds, fetched together:
- the Statuspage-compatible summary: every service, its status and its place on the page;
- incident.io's widget feed: ongoing incidents and maintenance, with the services they affect.

Plex hides uptime percentages on its page, so neither feed carries uptime.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import aiohttp

from plexbot.ui.embeds import Reply, card
from plexbot.ui.format import join, plural, timestamp, truncate
from plexbot.ui.theme import Colour, Limits

logger = logging.getLogger("plexbot.plex_status")

PAGE_URL = "https://status.plex.tv/"
SUMMARY_URL = "https://status.plex.tv/api/v2/summary.json"
WIDGET_URL = "https://status.plex.tv/api/v1/summary"

OPERATIONAL = "operational"
# Icon and short label per service status, from both feeds' vocabularies.
_STATUS = {
    OPERATIONAL: ("🟢", "operational"),
    "degraded_performance": ("🟡", "degraded"),
    "partial_outage": ("🟠", "partial outage"),
    "major_outage": ("🔴", "outage"),
    "full_outage": ("🔴", "outage"),
    "under_maintenance": ("🔧", "maintenance"),
}
# How many problems the /plex status card names before "and N more".
_CARD_PROBLEMS = 4
_CARD_INCIDENTS = 2


@dataclass
class Service:
    name: str
    status: str

    @property
    def icon(self) -> str:
        return _STATUS.get(self.status, ("⚪", ""))[0]

    @property
    def label(self) -> str:
        return _STATUS.get(self.status, ("", self.status.replace("_", " ")))[1]


@dataclass
class Incident:
    name: str
    url: Optional[str]
    since: Optional[datetime]
    maintenance: bool = False

    def line(self) -> str:
        name = truncate(self.name, 100)
        title = f"[{name}]({self.url})" if self.url else name
        prefix = "🔧 " if self.maintenance else ""
        return join([prefix + title, f"since {timestamp(self.since)}" if self.since else None])


@dataclass
class PlexStatus:
    indicator: str
    description: str
    services: List[Service]
    incidents: List[Incident] = field(default_factory=list)

    @property
    def problems(self) -> List[Service]:
        return [service for service in self.services if service.status != OPERATIONAL]

    @property
    def degraded(self) -> bool:
        return self.indicator not in ("", "none") or bool(self.problems) or bool(self.incidents)


def _parse_time(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_status(summary: Optional[dict], widget: Optional[dict]) -> Optional[PlexStatus]:
    """Combine the two feeds. None when the summary (the list of services) is unusable."""
    components = (summary or {}).get("components") or []
    if not components:
        return None
    components = sorted(components, key=lambda c: c.get("position") if c.get("position") is not None else 1e9)
    statuses = {c.get("id"): c.get("status") or OPERATIONAL for c in components}

    incidents = []
    widget = widget or {}
    for event in widget.get("ongoing_incidents") or []:
        incidents.append(Incident(event.get("name") or "Incident", event.get("url"),
                                  _parse_time(event.get("last_update_at"))))
        for component in event.get("affected_components") or []:
            if component.get("id") in statuses and component.get("current_status"):
                statuses[component["id"]] = component["current_status"]
    for event in widget.get("in_progress_maintenances") or []:
        incidents.append(Incident(event.get("name") or "Maintenance", event.get("url"),
                                  _parse_time(event.get("started_at")), maintenance=True))
        for component in event.get("affected_components") or []:
            if statuses.get(component.get("id")) == OPERATIONAL:
                statuses[component["id"]] = "under_maintenance"

    status = summary.get("status") or {}
    return PlexStatus(
        indicator=status.get("indicator") or "none",
        description=status.get("description") or "Unknown",
        services=[Service(c.get("name") or "Unnamed service", statuses[c.get("id")]) for c in components],
        incidents=incidents,
    )


async def _get_json(session: aiohttp.ClientSession, url: str) -> Optional[dict]:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                logger.warning(f"Plex status page returned HTTP {response.status} for {url}")
                return None
            return await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning(f"Could not fetch Plex service status from {url}: {e}")
        return None


async def fetch_status() -> Optional[PlexStatus]:
    """Both feeds, fetched concurrently; None if the service list can't be fetched."""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        summary, widget = await asyncio.gather(_get_json(session, SUMMARY_URL), _get_json(session, WIDGET_URL))
    return parse_status(summary, widget)


def card_summary(status: Optional[PlexStatus]) -> str:
    """The "Plex services" field on /plex status: one summary line, then only what's wrong."""
    if status is None:
        return "Status unavailable"
    total, problems = len(status.services), status.problems
    if not problems and not status.incidents:
        return f"🟢 All {plural(total, 'service')} operational"
    lines = [f"⚠️ {total - len(problems)} of {plural(total, 'service')} operational"]
    lines += [f"{s.icon} {truncate(s.name, 80)} · {s.label}" for s in problems[:_CARD_PROBLEMS]]
    if len(problems) > _CARD_PROBLEMS:
        lines.append(f"…and {len(problems) - _CARD_PROBLEMS} more")
    lines += [incident.line() for incident in status.incidents[:_CARD_INCIDENTS]]
    return truncate("\n".join(lines), Limits.FIELD_VALUE)


def services_card(status: PlexStatus, *, now: datetime) -> Reply:
    """Every service in the page's order, with its status, plus ongoing incidents."""
    lines = [f"{s.icon} {s.name}" + (f" · {s.label}" if s.status != OPERATIONAL else "") for s in status.services]
    embed = card(
        title="Plex services",
        description=truncate("\n".join(lines), Limits.DESCRIPTION),
        colour=Colour.WARNING if status.degraded else Colour.PLAYING,
        url=PAGE_URL,
    )
    if status.incidents:
        embed.add_field(
            name="Incidents",
            value=truncate("\n".join(i.line() for i in status.incidents), Limits.FIELD_VALUE),
            inline=False,
        )
    embed.set_footer(text=f"{status.description} · from status.plex.tv")
    embed.timestamp = now
    return Reply([embed])

