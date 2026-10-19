"""Server commands: /plex status, /plex watchers and /plexadmin logs."""

import asyncio
import io
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import nextcord
from nextcord.ext import commands, tasks

from plexbot.config import config
from plexbot.errors import LoggedModal, LoggedView
from plexbot.utilities import (
    config_secrets,
    library_totals,
    redact,
    ignored_usernames,
    viewer_avatar,
    viewer_mention,
)
from plexbot.plex_status import PlexStatus, card_summary, fetch_status, services_card
from plexbot.slash_commands import is_admin, listed, plex, plexadmin
from plexbot.version import bot_version, is_newer, latest_release
from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.cards import (
    plex_web_link, recent_viewer_entry, session_title, session_user, stream_detail, stream_overview,
)
from plexbot.ui.embeds import (
    ErrorKind, Reply, bot_token_handle, card, empty_state, error_state, service_down, set_thumbnail, success,
)
from plexbot.ui.format import file_size, is_truthy, join, plural, timestamp, to_int, truncate
from plexbot.ui.images import STRIP_POSTERS, current_frame, fetch_poster_strip, mascot, plex_image, poster_key
from plexbot.ui.theme import Colour

# Configure logging for this module
logger = logging.getLogger("plexbot.server_commands")

# --- /plexadmin logs helpers ---------------------------------------------------------

# A record starts with the "YYYY-MM-DD HH:MM:SS LEVEL " prefix from bot.py's log
# format; lines without it (traceback lines) belong to the record above.
_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
# Leaves room in Discord's 2000-char message limit for the header and code fences.
_PREVIEW_BUDGET = 1800
_HEADER_SEARCH_MAX = 60


def _split_records(text: str) -> list[str]:
    """Group log lines into records, keeping traceback lines with their record."""
    records: list[str] = []
    for line in text.splitlines():
        if _RECORD_START.match(line) or not records:
            records.append(line)
        else:
            records[-1] += "\n" + line
    return records


def _record_level(record: str) -> int | None:
    match = _RECORD_START.match(record)
    return _LEVELS[match.group(1).lower()] if match else None


def _filter_records(records: list[str], min_level: int | None, search: str | None) -> list[str]:
    needle = search.lower() if search else None
    matches = []
    for record in records:
        if min_level is not None:
            level = _record_level(record)
            if level is None or level < min_level:
                continue
        if needle and needle not in record.lower():
            continue
        matches.append(record)
    return matches


def _fit_tail(records: list[str], budget: int) -> list[str]:
    """Return the most recent records whose newline-joined length fits in budget.

    If even the newest record is too long, it is cut short rather than dropped.
    """
    shown: list[str] = []
    used = 0
    for record in reversed(records):
        cost = len(record) + (1 if shown else 0)
        if used + cost > budget:
            if not shown:
                shown.append(truncate(record, budget))
            break
        shown.append(record)
        used += cost
    return shown[::-1]


def _find_log_path(log: logging.Logger | None = None) -> Path | None:
    """Locate the file the bot logs to, from the first FileHandler up the logger tree."""
    current = log or logging.getLogger()
    while current:
        for handler in current.handlers:
            if isinstance(handler, logging.FileHandler):
                return Path(handler.baseFilename)
        if not current.propagate:
            break
        current = current.parent
    return None


def _build_logs_reply(text: str, min_level: int | None, search: str | None):
    """Build the /plexadmin logs message and attachments as (content, [(filename, bytes), ...])."""
    records = _split_records(text)
    filtered = min_level is not None or search is not None
    matches = _filter_records(records, min_level, search)
    # Escape code fences so a logged ``` can't break out of the preview block.
    shown = _fit_tail([r.replace("```", "``​`") for r in matches], _PREVIEW_BUDGET)

    full_log = text.encode("utf-8")
    files = [("plexbot.log", full_log)]
    attached = f"full log attached ({file_size(len(full_log))})"
    if filtered:
        parts = []
        if min_level is not None:
            parts.append(f"{logging.getLevelName(min_level)}+")
        if search:
            parts.append(f'matching "{truncate(search, _HEADER_SEARCH_MAX)}"')
        description = " ".join(parts)
        if not matches:
            header = f"No log records **{description}** · {attached}"
        else:
            count = f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
            header = f"**{description}** · {count}, showing last {len(shown)} · {attached}"
        if len(matches) > len(shown):
            files.append(("plexbot-matches.log", ("\n".join(matches) + "\n").encode("utf-8")))
    else:
        header = f"Last {len(shown)} of {len(records)} log records · {attached}"

    content = header + ("\n```\n" + "\n".join(shown) + "\n```" if shown else "")
    return content, files


# --- /plex watchers helpers -----------------------------------------------------

_NO_STREAM_PERMISSION = "Only stream managers can stop streams."
# Discord caps autocomplete results and select options at 25, and their labels at 100 chars.
_MAX_CHOICES = 25
_MAX_LABEL = 100
_LAST_VIEWERS = 3


def last_viewers(rows: list[dict], ignored: set, limit: int = _LAST_VIEWERS) -> list[dict]:
    """Each viewer's latest play from newest-first history, skipping ignored users."""
    seen, picked = set(), []
    for row in rows:
        user = row.get("user") or row.get("friendly_name")
        if not user or user in ignored or user in seen:
            continue
        seen.add(user)
        picked.append(row)
        if len(picked) == limit:
            break
    return picked


def _session_label(session: dict, suffix: str = "") -> str:
    """One-line "user — title" label for a stream, within Discord's label limit."""
    label = f"{session_user(session)} — {session_title(session)}"
    return truncate(label, _MAX_LABEL - len(suffix)) + suffix


def _session_choices(sessions: list[dict], query: str) -> dict[str, str]:
    """Map stream labels to session IDs for autocomplete / select menus, filtered by query."""
    needle = (query or "").lower()
    choices: dict[str, str] = {}
    for session in sessions:
        session_id = session.get("session_id")
        if not session_id:
            continue
        label = _session_label(session)
        if needle and needle not in label.lower():
            continue
        n = 2
        while label in choices:  # same user watching the same title on two devices
            label = _session_label(session, f" ({n})")
            n += 1
        choices[label] = str(session_id)
        if len(choices) == _MAX_CHOICES:
            break
    return choices


def _can_manage_streams(user, guild_id, manager_ids) -> bool:
    """Bot admins, plus any user or role listed in discord.stream_manager_ids, may stop streams."""
    return is_admin(user, guild_id) or listed(user, manager_ids)


def _find_session(activity: dict, session_id: str) -> dict | None:
    return next((s for s in activity.get("sessions", []) if str(s.get("session_id")) == session_id), None)


class StopStreamModal(LoggedModal):
    """Asks for an optional message to the viewer, then stops the stream."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView"):
        super().__init__(title=truncate(f"Stop: {label}", 45), timeout=300)
        self.cog = cog
        self.session_id = session_id
        self.watchers_view = watchers_view
        self.message_input = nextcord.ui.TextInput(
            label="Message to the viewer (optional)",
            style=nextcord.TextInputStyle.paragraph,
            required=False,
            max_length=200,
            placeholder="Shown on their Plex client when the stream stops",
        )
        self.add_item(self.message_input)

    async def callback(self, interaction: nextcord.Interaction):
        # Opened from the private detail card: acknowledge without a new message, then
        # edit that card into the result.
        await interaction.response.defer()
        ok, text = await self.cog.stop_stream(interaction.user, self.session_id, self.message_input.value)
        reply = success(text) if ok else error_state(ErrorKind.INPUT, text)
        await reply.edit(interaction)
        # Plex takes a moment to drop a stopped session from its activity list.
        await asyncio.sleep(2)
        await self.watchers_view.rerender()


class StreamDetailView(LoggedView):
    """Controls on a private stream detail card: Stop, for stream managers only."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView",
                 can_stop: bool):
        super().__init__(timeout=600)
        self.cog = cog
        self.session_id = session_id
        self.label = label
        self.watchers_view = watchers_view
        if not can_stop:
            self.remove_item(self.stop_button)

    @nextcord.ui.button(label="Stop this stream", emoji="\U0001F6D1", style=nextcord.ButtonStyle.danger)
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self.cog.can_manage(interaction):
            await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(interaction, ephemeral=True)
            return
        await interaction.response.send_modal(
            StopStreamModal(self.cog, self.session_id, self.label, self.watchers_view)
        )


class StreamDetailsSelect(nextcord.ui.StringSelect):
    def __init__(self, choices: dict[str, str]):
        options = [nextcord.SelectOption(label=label, value=sid) for label, sid in choices.items()]
        # nextcord 3.2 StringSelect never generates a custom_id (MISSING, not None), and Discord
        # rejects a select without one.
        super().__init__(custom_id=os.urandom(16).hex(), placeholder="Stream details…",
                         options=options, row=0)

    async def callback(self, interaction: nextcord.Interaction):
        watchers_view: WatchersView = self.view
        # The clicked message is edited with the bot token, which (unlike the command's
        # interaction webhook) doesn't expire after 15 minutes.
        watchers_view.message = interaction.message
        await watchers_view.cog.show_stream_detail(interaction, self.values[0], watchers_view)
        # Opening a detail card leaves the public message alone, so Discord would keep this
        # choice selected locally and picking it again would send nothing. Re-send the view to reset it.
        try:
            await interaction.message.edit(view=watchers_view)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not reset the /plex watchers dropdown: {e}")


class WatchersView(LoggedView):
    """Controls under the public /plex watchers overview: stream details (anyone) and Refresh."""

    def __init__(self, cog: "ServerCommands", sessions: list, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.set_sessions(sessions)

    def set_sessions(self, sessions: list) -> None:
        for item in [child for child in self.children if isinstance(child, StreamDetailsSelect)]:
            self.remove_item(item)
        choices = _session_choices(sessions, "")
        if choices:
            self.add_item(StreamDetailsSelect(choices))

    # Not named refresh(): nextcord calls View.refresh(components) itself when the
    # message is edited, and overriding it crashed the gateway connection.
    async def rerender(self):
        """Re-render the public overview, e.g. after a stream was stopped or ended."""
        if self.message is None:
            return
        activity = await self.cog.fetch_activity()
        if activity is None:
            return
        reply = await self.cog.render_watchers(activity, self)
        try:
            await reply.edit_message(self.message)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not refresh /plex watchers message: {e}")

    @nextcord.ui.button(label="Refresh", emoji="\U0001F504", style=nextcord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer()
        self.message = interaction.message  # bot-token handle; the command's webhook expires
        activity = await self.cog.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        reply = await self.cog.render_watchers(activity, self)
        await reply.edit(interaction)


# --- /plex status helpers ----------------------------------------------------


# Library sizes: Tautulli's media info is slow to build the first time, so wait briefly and
# remember what came back. A library that timed out is asked again next time.
_SIZE_WAIT = 4
_SIZE_TTL = 6 * 3600
_SIZED_KINDS = ("movie", "show", "artist")


def _version_number(version) -> str:
    """"1.43.4.10903-e5521bd8c" -> "1.43.4.10903": the build hash is noise."""
    return str(version or "").split("-")[0]


def version_line(current, update: Optional[dict]) -> Optional[str]:
    """Whether the server is up to date, from Tautulli's check against plex.tv's releases."""
    if not update or not update.get("version"):
        return None
    released = to_int(update.get("release_date"))
    latest = _version_number(update["version"])
    if is_truthy(update.get("update_available")):
        return join([f"⬆️ Update available: {latest}", f"released {timestamp(released)}" if released else None])
    # Only the latest release's date is known; a newer (beta) server isn't that release.
    same = latest == _version_number(current)
    return join(["✅ Up to date", f"released {timestamp(released, 'D')}" if released and same else None])


# Size comparisons for the status footer, with real-world figures. One is picked at random
# on every /plex status.
_FLOPPY_BYTES, _FLOPPY_MM, _FLOPPY_LONG_M, _FLOPPY_G = 1_474_560, 3.3, 0.09, 20  # 3.5" HD floppy
_CD_BYTES, _DISC_MM = 734_003_200, 1.2  # a 700 MB CD; CDs, DVDs and Blu-rays are all 1.2 mm thick
_DVD_BYTES, _BLURAY_BYTES = 4_700_000_000, 25_000_000_000  # single-layer discs
_PHONE_BYTES = 128_000_000_000
_ELEPHANT_KG = 6_000
_CONNECTIONS = [
    (0.056, "on a 56k dial-up modem"),
    (8, "on 8 Mbps ADSL"),
    (100, "at 100 Mbps"),
    (1_000, "on gigabit fibre"),
]
_EDGE_OF_SPACE_M = 100_000  # the Kármán line
_LANDMARKS = [(8_849, "Mount Everest"), (828, "the Burj Khalifa"), (330, "the Eiffel Tower"),
              (96, "Big Ben"), (46, "the Statue of Liberty"), (5.5, "a giraffe"), (1.7, "a person")]


def _count(n: float) -> str:
    return f"{n / 1e6:.1f} million" if n >= 1e6 else f"{n:,.0f}"


def _distance(metres: float) -> str:
    if metres >= 10_000:
        return f"{metres / 1000:,.0f} km"
    return f"{metres:,.0f} m" if metres >= 10 else f"{metres:.1f} m"


def _stacked(metres: float) -> str:
    if metres >= _EDGE_OF_SPACE_M:
        return f"stacked {_distance(metres)} high, past the edge of space"
    for height, name in _LANDMARKS:
        if metres >= height:
            times = metres / height
            return f"stacked {_distance(metres)} high, " + (
                f"taller than {name}" if times < 2 else f"{times:,.0f}× the height of {name}"
            )
    return f"stacked {_distance(metres)} high"


def _weight(kg: float) -> str:
    if kg < _ELEPHANT_KG:
        return f"{kg:,.0f} kg of floppy disks"
    return f"{kg / 1000:,.0f} tonnes of floppy disks, as heavy as {kg / _ELEPHANT_KG:,.0f} elephants"


def _download(num_bytes: int, mbps: float, connection: str) -> str:
    seconds = num_bytes * 8 / (mbps * 1_000_000)
    if seconds < 3600:
        span = plural(max(round(seconds / 60), 1), "minute")
    elif seconds < 48 * 3600:
        span = plural(round(seconds / 3600), "hour")
    elif seconds < 365 * 86400:
        span = f"{seconds / 86400:,.1f} days"
    else:
        span = f"{seconds / (365 * 86400):,.1f} years"
    return f"{span} of non-stop downloading {connection}"


def _discs(n: float, kind: str) -> Optional[str]:
    if n < 2:
        return None
    height = n * _DISC_MM / 1000
    return f"about {_count(n)} {kind}" + (f", {_stacked(height)}" if height >= _LANDMARKS[-1][0] else "")


def size_comparisons(total: int) -> list:
    """Every comparison for this size, as text: what /plex status picks from."""
    floppies, phones = total / _FLOPPY_BYTES, total / _PHONE_BYTES
    lines = [
        f"about {_count(floppies)} floppy disks, {_stacked(floppies * _FLOPPY_MM / 1000)}",
        f"about {_count(floppies)} floppy disks, laid end to end for {_distance(floppies * _FLOPPY_LONG_M)}",
        _weight(floppies * _FLOPPY_G / 1000),
        _discs(total / _CD_BYTES, "CDs"),
        _discs(total / _DVD_BYTES, "DVDs"),
        _discs(total / _BLURAY_BYTES, "Blu-rays"),
        f"enough to fill {_count(phones)} phones with 128 GB each" if phones >= 2 else None,
    ]
    lines += [_download(total, mbps, connection) for mbps, connection in _CONNECTIONS]
    return [line for line in lines if line]


def size_comparison(total: int, pick=random.choice) -> Optional[str]:
    """"8.1 TB of media: about 5.5 million floppy disks, stacked 18 km high, …" (one at random)."""
    if not total or total <= 0:
        return None
    return f"{file_size(total)} of media: " + pick(size_comparisons(total))


def build_status_reply(server: dict, libraries, activity: Optional[dict], update: Optional[dict],
                       plex_status: Optional[PlexStatus], *, now: datetime,
                       sizes: Optional[dict] = None, bot_version: Optional[str] = None,
                       pick=random.choice) -> Reply:
    sizes = sizes or {}
    plex_pass = " · \U0001F39F\ufe0f Plex Pass" if to_int(server.get("pms_plexpass")) else ""
    version = server.get("pms_version")
    embed = card(
        title=server.get("pms_name") or "Plex Media Server",
        description=join([
            f"Plex Media Server {_version_number(version) or 'unknown version'} "
            f"on {server.get('pms_platform') or 'unknown platform'}{plex_pass}",
            version_line(version, update),
        ], sep="\n"),
        colour=Colour.WARNING if plex_status is not None and plex_status.degraded else Colour.PLAYING,
    )

    def size(kind):
        return file_size(sizes[kind]) if sizes.get(kind) else None

    totals = library_totals(libraries)
    library = [
        join([f"\U0001F3AC {plural(totals['movies'], 'movie')}", size("movie")]) if totals["movies"] else None,
        join([f"\U0001F4FA {plural(totals['shows'], 'show')}", plural(totals['episodes'], 'episode'),
              size("show")]) if totals["shows"] else None,
        join([f"\U0001F3B5 {plural(totals['artists'], 'artist')}", plural(totals['tracks'], 'track'),
              size("artist")]) if totals["artists"] else None,
    ]
    library = [line for line in library if line]
    if library:
        embed.add_field(name="Library", value="\n".join(library), inline=True)

    if activity is None:
        watching = "Unavailable"
    else:
        streams = to_int(activity.get("stream_count"))
        bandwidth = to_int(activity.get("total_bandwidth"))
        watching = (
            join([plural(streams, "stream"), f"{bandwidth / 1000:.1f} Mbps" if bandwidth else None])
            if streams else "Nobody's watching"
        )
    embed.add_field(name="Activity", value=watching, inline=True)
    embed.add_field(name="Plex services", value=card_summary(plex_status), inline=False)

    footer = join([size_comparison(sum(sizes.values()), pick=pick), f"PlexBot {bot_version}" if bot_version else None])
    if footer:
        embed.set_footer(text=footer)
    embed.timestamp = now
    art = mascot("mascot-server")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


class StatusView(LoggedView):
    """Under the public /plex status card: the full list of plex.tv services, privately."""

    def __init__(self, plex_status: PlexStatus, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.plex_status = plex_status

    @nextcord.ui.button(label="Plex services", emoji="\U0001F4E1", style=nextcord.ButtonStyle.secondary)
    async def services_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True, with_message=True)
        latest = await fetch_status()
        if latest is not None:
            self.plex_status = latest
        await services_card(self.plex_status, now=nextcord.utils.utcnow()).send(interaction, ephemeral=True)


class ServerCommands(commands.Cog):
    help_category = "watching"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self._section_sizes: dict[str, tuple[float, int]] = {}  # section_id -> (fetched at, bytes)
        self._display_streams = True  # presence alternates between stream stats and a help hint
        self._startup_log: Optional[asyncio.Task] = None
        self.status_task.start()  # type: ignore[attr-defined]

    async def _log_startup_info(self):
        """Log Tautulli connectivity, and whether a newer release is out, once, at startup."""
        try:
            r = await self.tautulli.get_home_stats()
            if Tautulli.check_response(r):
                logger.info(f"Logged in as {self.bot.user}")
                logger.info("Connection to Tautulli successful")
            else:
                logger.critical("Connection to Tautulli failed")
        except Exception as e:
            logger.error(f"Error during startup info logging: {e}")

        # Never raises: a GitHub that can't answer gives None.
        latest = await latest_release()
        if is_newer(latest, bot_version()):
            logger.info(f"PlexBot {latest} is out (running {bot_version()}): https://github.com/brah/plex-bot/releases")

    def cog_unload(self):
        # Stop the background presence loop. The shared Tautulli client is owned by
        # the bot (PlexBot.close()) and must not be closed here.
        self.status_task.cancel()  # type: ignore[attr-defined]
        if self._startup_log:
            self._startup_log.cancel()

    @tasks.loop(seconds=15)
    async def status_task(self):
        """Update the bot's presence, alternating between stream stats and a help hint."""
        try:
            response = await self.tautulli.get_activity()
            if not Tautulli.check_response(response):
                logger.error("Failed to retrieve activity from Tautulli.")
                return
            data = Tautulli.get_response_data(response, {})
            # Total, not WAN, bandwidth: the same figure /plex status and /plex watchers show.
            streams = plural(to_int(data.get("stream_count")), "stream")
            mbps = to_int(data.get("total_bandwidth")) / 1000

            if self._display_streams:
                activity_text = f"{streams} at {mbps:.1f} Mbps"
                activity_type = nextcord.ActivityType.playing
            else:
                activity_text = "/plex help"
                activity_type = nextcord.ActivityType.listening

            await self.bot.change_presence(
                activity=nextcord.Activity(type=activity_type, name=activity_text)
            )
            self._display_streams = not self._display_streams  # Toggle the display mode
        except Exception as e:
            logger.error(f"Error in status_task(): {e}")

    @status_task.before_loop  # type: ignore[attr-defined]
    async def _before_status_task(self):
        """Wait for the gateway, then log startup info alongside the first tick."""
        await self.bot.wait_until_ready()
        # In the background: a slow Tautulli or GitHub mustn't hold up the first presence update.
        self._startup_log = asyncio.create_task(self._log_startup_info())

    # --- /plex status ------------------------------------------------------

    async def _section_size(self, section_id: str) -> Optional[int]:
        # total_file_size sums the sizes Tautulli has cached. Movie sizes come with its scan; TV and
        # music sizes only after "Calculate Total File Sizes" on the library's Media Info tab.
        try:
            response = await asyncio.wait_for(
                self.tautulli.get_library_media_info(section_id=section_id, length=1), _SIZE_WAIT
            )
        except asyncio.TimeoutError:
            logger.info(f"Library {section_id} size not ready yet (Tautulli is still scanning it)")
            return None
        if not Tautulli.check_response(response):
            return None
        return to_int(Tautulli.get_response_data(response, {}).get("total_file_size"))

    async def _library_sizes(self, rows) -> dict:
        """Bytes per library kind, counting only libraries Tautulli has sized."""
        now = time.monotonic()
        sections = [(str(row["section_id"]), row.get("section_type")) for row in rows or []
                    if row.get("section_type") in _SIZED_KINDS and row.get("section_id") not in (None, "")]
        stale = [sid for sid, _ in sections
                 if sid not in self._section_sizes or now - self._section_sizes[sid][0] > _SIZE_TTL]
        if stale:
            for sid, size in zip(stale, await asyncio.gather(*(self._section_size(sid) for sid in stale))):
                if size is not None:
                    self._section_sizes[sid] = (now, size)
        totals: dict = {}
        for sid, kind in sections:
            size = self._section_sizes.get(sid, (0, 0))[1]
            if size:
                totals[kind] = totals.get(kind, 0) + size
        return totals

    @plex.subcommand(description="Show how the Plex server is doing: library, activity, updates")
    async def status(self, interaction: nextcord.Interaction):
        await interaction.response.defer()

        def data(response, default):
            if Tautulli.check_response(response):
                return Tautulli.get_response_data(response, default)
            return default

        async def libraries_and_sizes():
            rows = data(await self.tautulli.get_libraries(), [])
            return rows, await self._library_sizes(rows)

        server_r, (libraries, sizes), activity_r, update_r, plex_status = await asyncio.gather(
            self.tautulli.get_server_info(),
            libraries_and_sizes(),
            self.tautulli.get_activity(),
            self.tautulli.get_pms_update(),
            fetch_status(),
        )
        if not Tautulli.check_response(server_r):
            logger.error("Failed to retrieve server info from Tautulli.")
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        server = Tautulli.get_response_data(server_r, {})

        reply = build_status_reply(
            server, libraries, data(activity_r, None), data(update_r, None), plex_status,
            now=nextcord.utils.utcnow(), sizes=sizes, bot_version=bot_version(),
        )
        if plex_status is not None:
            reply.view = StatusView(plex_status)
        message = await reply.send(interaction)
        if reply.view is not None:
            reply.view.message = bot_token_handle(interaction, message)

    # --- /plex watchers ----------------------------------------------------

    def can_manage(self, interaction: nextcord.Interaction) -> bool:
        return _can_manage_streams(interaction.user, interaction.guild_id,
                                   config.get("discord", "stream_manager_ids", []))

    async def fetch_activity(self) -> dict | None:
        response = await self.tautulli.get_activity()
        if not Tautulli.check_response(response):
            logger.error("Failed to retrieve activity from Tautulli.")
            return None
        return Tautulli.get_response_data(response, {})

    def _mention(self, row: dict) -> str:
        return viewer_mention(self.bot, row)

    def _visible_sessions(self, activity: dict) -> list:
        ignored = ignored_usernames()
        return [s for s in activity.get("sessions", []) if s.get("username") not in ignored]

    async def _nobody_watching(self) -> str:
        """The empty overview's text, with recent viewers when discord.show_recent_viewers is on."""
        message = "Nobody's watching right now."
        if not is_truthy(config.get("discord", "show_recent_viewers", False)):
            return message
        response = await self.tautulli.get_history({"length": 25, "order_column": "date", "order_dir": "desc"})
        if not Tautulli.check_response(response):
            return message
        rows = last_viewers(Tautulli.get_response_data(response, {}).get("data") or [], ignored_usernames())
        if not rows:
            return message
        return f"{message}\n\n**Last seen**\n" + "\n".join(
            recent_viewer_entry(row, user=self._mention(row)) for row in rows
        )

    async def render_watchers(self, activity: dict, view: "WatchersView") -> Reply:
        """The public overview for the current activity, with its controls rebuilt to match."""
        sessions = self._visible_sessions(activity)
        view.set_sessions(sessions)
        if not sessions:
            reply = empty_state(await self._nobody_watching(), hint="Hit Refresh to check again.")
            reply.view = view
            return reply
        strip = await fetch_poster_strip(self.tautulli, [poster_key(s) for s in sessions],
                                         extra=max(0, len(sessions) - STRIP_POSTERS))
        embed = stream_overview(
            sessions, bandwidth_kbps=to_int(activity.get("total_bandwidth")), strip=strip,
            updated=nextcord.utils.utcnow(), name=self._mention,
        )
        return Reply([embed], [strip] if strip else [], view=view)

    async def show_stream_detail(self, interaction: nextcord.Interaction, session_id: str,
                                 watchers_view: "WatchersView") -> None:
        await interaction.response.defer(ephemeral=True, with_message=True)
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        session = _find_session(activity, session_id)
        if session is None:
            await empty_state("That stream has ended.", hint="The list has been refreshed.").send(
                interaction, ephemeral=True
            )
            await watchers_view.rerender()
            return

        frame, poster = await asyncio.gather(
            current_frame(self.tautulli, session, filename="frame.jpg"),
            plex_image(self.tautulli, poster_key(session), filename="poster.jpg"),
        )
        if frame is None:
            frame = await plex_image(
                self.tautulli, session.get("art"), filename="art.jpg", width=640, height=360
            )
        embed = stream_detail(
            session,
            avatar_url=viewer_avatar(self.bot, session),
            link=plex_web_link(await self.tautulli.server_id(), session.get("rating_key")),
            frame=frame,
            poster=poster,
            now=nextcord.utils.utcnow(),
        )
        view = StreamDetailView(self, session_id, _session_label(session), watchers_view,
                                can_stop=self.can_manage(interaction))
        view.message = await Reply([embed], [a for a in (frame, poster) if a], view=view).send(
            interaction, ephemeral=True)

    async def stop_stream(self, actor, session_id: str, message: str | None) -> tuple[bool, str]:
        """Stop a stream; returns (stopped, text for the manager who asked)."""
        activity = await self.fetch_activity()
        if activity is None:
            return False, "Couldn't reach Tautulli to stop that stream. Try again in a moment."
        session = _find_session(activity, session_id)
        if session is None:
            return False, "That stream isn't running anymore. Run `/plex watchers` to see current streams."

        label = _session_label(session)
        status = await self.tautulli.terminate_session(session_id=session_id, message=message or None)
        if status == 200:
            note = f" with message: {message}" if message else ""
            logger.info(f"{actor} stopped stream {session_id} ({label}){note}")
            return True, f"Stopped **{self._mention(session)}**'s stream of **{session_title(session)}**."
        logger.warning(f"{actor} failed to stop stream {session_id} ({label}): Tautulli returned an error")
        return False, (
            "Tautulli couldn't stop that stream. An admin can check `/plexadmin logs` for details."
        )

    @plex.subcommand(description="See who's watching on Plex. Stream managers can also stop streams.")
    async def watchers(
        self,
        interaction: nextcord.Interaction,
        stop: str = nextcord.SlashOption(
            description="A stream to stop (stream managers only)", required=False, autocomplete=True
        ),
        message: str = nextcord.SlashOption(
            description="Message shown to the viewer when their stream is stopped",
            required=False,
            max_length=200,
        ),
    ):
        if stop:
            if not self.can_manage(interaction):
                await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(
                    interaction, ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            ok, text = await self.stop_stream(interaction.user, stop, message)
            reply = success(text) if ok else error_state(ErrorKind.INPUT, text)
            await reply.send(interaction, ephemeral=True)
            return

        await interaction.response.defer()
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view = WatchersView(self, [])
        reply = await self.render_watchers(activity, view)
        view.message = bot_token_handle(interaction, await reply.send(interaction))

    @watchers.on_autocomplete("stop")
    async def _watchers_stop_autocomplete(self, interaction: nextcord.Interaction, stop: str):
        if not self.can_manage(interaction):
            await interaction.response.send_autocomplete({})
            return
        activity = await self.fetch_activity()
        sessions = activity.get("sessions", []) if activity else []
        await interaction.response.send_autocomplete(_session_choices(sessions, stop))

    # --- /plexadmin logs ---------------------------------------------------

    # Admin-only and guild-only via the /plexadmin group.
    @plexadmin.subcommand(description="Show recent bot logs and attach the full log file")
    async def logs(
        self,
        interaction: nextcord.Interaction,
        level: str = nextcord.SlashOption(
            description="Only show records at this level or above",
            required=False,
            choices={name.title(): name for name in _LEVELS},
        ),
        search: str = nextcord.SlashOption(
            description="Only show records containing this text", required=False, max_length=100
        ),
    ):
        await interaction.response.defer(ephemeral=True)

        log_path = _find_log_path()
        if log_path is None or not log_path.exists():
            await interaction.followup.send("No log file found.", ephemeral=True)
            return
        raw = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
        # Records are redacted as they're logged; this also covers lines written before that.
        text = redact(raw, config_secrets())
        if not text.strip():
            await interaction.followup.send("The log file is empty.", ephemeral=True)
            return

        content, files = _build_logs_reply(text, _LEVELS.get(level) if level else None, search or None)
        await interaction.followup.send(
            content,
            files=[nextcord.File(io.BytesIO(data), filename=name) for name, data in files],
            ephemeral=True,
        )


def setup(bot):
    bot.add_cog(ServerCommands(bot))
