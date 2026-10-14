# ui/cards.py

"""Renderers for the /plex watchers stream cards and the media card (lookups, random, recent)."""

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from urllib.parse import quote

import nextcord
from nextcord.utils import escape_markdown

from ui.embeds import card, set_image, set_thumbnail
from ui.format import format_ms, join, plural, progress_bar, timestamp, to_int, truncate
from ui.images import Attachment, dominant_colour
from ui.theme import Colour, Emoji, Limits, media_colour

_DECISIONS = {"direct play": "Direct Play", "copy": "Direct Stream", "transcode": "Transcode"}
_SUBTITLE_DECISIONS = {"burn": "Burned in", "copy": "Direct Stream", "transcode": "Transcode"}
_STATE_ICONS = {"playing": Emoji.PLAYING, "paused": Emoji.PAUSED, "buffering": Emoji.BUFFERING}
_LOCATIONS = {"lan": "Local", "wan": "Remote", "cellular": "Mobile data"}


def session_user(session: dict) -> str:
    return session.get("friendly_name") or session.get("username") or session.get("user") or "Unknown"


def session_title(session: dict) -> str:
    title = session.get("title") or session.get("full_title") or "Unknown"
    media_type = session.get("media_type")
    if media_type == "episode":
        season = to_int(session.get("parent_media_index"), -1)
        episode = to_int(session.get("media_index"), -1)
        code = f"S{season:02d}E{episode:02d}" if season >= 0 and episode >= 0 else None
        return join([session.get("grandparent_title"), code, title])
    if media_type == "season":
        return join([session.get("parent_title"), title])
    if media_type == "track":
        return join([session.get("grandparent_title"), title], " — ")
    if media_type == "album":
        return join([session.get("parent_title"), title], " — ")
    year = session.get("year")
    return f"{title} ({year})" if year else title


_MEDIA_ICONS = {
    "movie": "\U0001F3AC", "show": "\U0001F4FA", "season": "\U0001F4FA", "episode": "\U0001F4FA",
    "artist": "\U0001F3B5", "album": "\U0001F3B5", "track": "\U0001F3B5",
}
_MEDIA_NAMES = {
    "movie": "Movie", "show": "TV show", "season": "Season", "episode": "Episode",
    "artist": "Artist", "album": "Album", "track": "Track",
}
_FINISHED_PERCENT = 90  # below this, a play reads as "stopped at 45%"


def media_icon(media_type) -> str:
    return _MEDIA_ICONS.get(media_type or "", Emoji.PLAYING)


def recent_viewer_line(row: dict, *, user: Optional[str] = None, show_user: bool = True) -> str:
    """One past play (a Tautulli history row): who, what, how far, where, when.

    user: the name to show (e.g. their Discord name); show_user=False leads with the title instead,
    for a list that is all one person's.
    """
    percent = to_int(row.get("percent_complete"), 100)
    when = to_int(row.get("stopped")) or to_int(row.get("date"))
    title = escape_markdown(truncate(session_title(row), 80))
    who = escape_markdown(user or session_user(row))
    return join([
        f"{media_icon(row.get('media_type'))} **{who if show_user else title}**",
        title if show_user else None,
        f"stopped at {percent}%" if percent < _FINISHED_PERCENT else None,
        f"on {escape_markdown(str(row['platform']))}" if row.get("platform") else None,
        timestamp(when) if when else None,
    ])


def _state(session: dict) -> str:
    return str(session.get("state") or "").lower()


def _decision(value) -> Optional[str]:
    return _DECISIONS.get(str(value or "").lower())


def _progress(session: dict) -> str:
    offset, duration = to_int(session.get("view_offset")), to_int(session.get("duration"))
    if duration <= 0:
        return format_ms(offset) if offset else ""
    return f"`{progress_bar(offset / duration)}` {format_ms(offset)} / {format_ms(duration)}"


def session_lines(session: dict) -> str:
    """Two lines per stream for the overview: who and what, then progress, quality, device."""
    icon = _STATE_ICONS.get(_state(session), Emoji.BULLET)
    user = escape_markdown(session_user(session))
    title = escape_markdown(session_title(session))
    quality = join([
        session.get("stream_video_full_resolution"),
        _decision(session.get("transcode_decision")),
    ], " ")
    device = session.get("player") or session.get("product") or session.get("platform")
    details = join([_progress(session), quality, device])
    return f"{icon} **{user}** · {title}" + (f"\n{details}" if details else "")


def _state_colour(sessions: Sequence[dict]) -> int:
    return Colour.PLAYING if any(_state(s) == "playing" for s in sessions) else Colour.WARNING


def stream_overview(sessions: Sequence[dict], *, bandwidth_kbps: int, strip: Optional[Attachment],
                    updated: datetime) -> nextcord.Embed:
    header = join([
        f"**{plural(len(sessions), 'stream')}**",
        f"{bandwidth_kbps / 1000:.1f} Mbps" if bandwidth_kbps else None,
        f"updated {timestamp(updated)}",
    ])
    blocks = [header]
    used = len(header)
    reserve = 40  # room for the "...and N more" line
    for index, session in enumerate(sessions):
        block = session_lines(session)
        if used + 2 + len(block) > Limits.DESCRIPTION - reserve:
            blocks.append(f"…and {plural(len(sessions) - index, 'more stream')}")
            break
        blocks.append(block)
        used += 2 + len(block)
    embed = card(title="Now playing", description="\n\n".join(blocks), colour=_state_colour(sessions))
    set_image(embed, strip)
    embed.set_footer(text="Pick a stream below for the details")
    return embed


def plex_web_link(server_id: Optional[str], rating_key) -> Optional[str]:
    if not server_id or not rating_key:
        return None
    key = quote(f"/library/metadata/{rating_key}", safe="")
    return f"https://app.plex.tv/desktop/#!/server/{server_id}/details?key={key}"


def _video_line(session: dict) -> Optional[str]:
    source, stream = session.get("video_full_resolution"), session.get("stream_video_full_resolution")
    if not (source or stream):
        return None
    resolution = source if not stream or stream == source else f"{source} → {stream}"
    codec = (session.get("video_codec") or "").upper()
    return "**Video** " + join([join([resolution, codec], " "), _decision(session.get("video_decision"))])


def _audio_line(session: dict) -> Optional[str]:
    codec = (session.get("audio_codec") or "").upper()
    if not codec:
        return None
    sound = join([codec, session.get("audio_channel_layout")], " ")
    return "**Audio** " + join([sound, _decision(session.get("audio_decision"))])


def _subtitle_line(session: dict) -> Optional[str]:
    language, codec = session.get("subtitle_language"), (session.get("subtitle_codec") or "").upper()
    if not (language or codec):
        return None
    label = f"{language} ({codec})" if language and codec else (language or codec)
    decision = _SUBTITLE_DECISIONS.get(str(session.get("subtitle_decision") or "").lower())
    return "**Subtitles** " + join([label, decision])


def stream_detail(session: dict, *, avatar_url: Optional[str], link: Optional[str],
                  frame: Optional[Attachment], poster: Optional[Attachment],
                  now: datetime) -> nextcord.Embed:
    """The private per-stream card. Never includes the viewer's IP address or location."""
    state = _state(session)
    embed = card(title=session_title(session), url=link, colour=_state_colour([session]))
    embed.set_author(name=truncate(session_user(session), Limits.AUTHOR), icon_url=avatar_url or None)

    progress = _progress(session)
    offset, duration = to_int(session.get("view_offset")), to_int(session.get("duration"))
    if state == "paused":
        progress = join([progress, "Paused"], "\n")
    elif state == "playing" and duration > offset:
        ends = now + timedelta(milliseconds=duration - offset)
        progress = join([progress, f"Ends {timestamp(ends, 't')}"], "\n")
    embed.add_field(name="Progress", value=truncate(progress or "Unknown", Limits.FIELD_VALUE),
                    inline=False)

    stream = join([_video_line(session), _audio_line(session), _subtitle_line(session)], "\n")
    if stream:
        embed.add_field(name="Stream", value=truncate(stream, Limits.FIELD_VALUE), inline=False)

    bandwidth = to_int(session.get("bandwidth"))
    player = join([
        join([session.get("player"), session.get("product")]),
        session.get("platform"),
        _LOCATIONS.get(str(session.get("location") or "").lower()),
        f"{bandwidth / 1000:.1f} Mbps" if bandwidth else None,
    ], "\n")
    if player:
        embed.add_field(name="Player", value=truncate(player, Limits.FIELD_VALUE), inline=False)

    set_image(embed, frame)
    set_thumbnail(embed, poster)
    return embed


# --- media cards: lookups, random picks, recently added ----------------------

_SUMMARY_LENGTH = 350
_CREDITS = 3  # directors / actors named on a card


def runtime(ms) -> Optional[str]:
    """Milliseconds as "2h 16m" or "45m"; None when unknown."""
    minutes = to_int(ms) // 60000
    if minutes <= 0:
        return None
    hours, minutes = divmod(minutes, 60)
    return join([f"{hours}h" if hours else None, f"{minutes}m" if minutes else None], " ")


def _star_rating(item: dict) -> Optional[str]:
    for key in ("audience_rating", "rating"):
        try:
            value = float(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return f"⭐ {value:.1f}"
    return None


def _air_date(value) -> Optional[str]:
    try:
        day = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return timestamp(day, "D")


def _names(values, limit: int) -> list:
    return [str(value) for value in (values or [])[:limit] if value]


def _genre(name) -> str:
    """Tautulli genres are "Action"; the media cache stores some lower-case."""
    name = str(name)
    return name[:1].upper() + name[1:]


def media_facts(item: dict) -> str:
    """One line of facts: "R · 2h 16m · Action, Science Fiction · ⭐ 8.7"."""
    media_type = item.get("media_type")
    seasons = to_int(item.get("children_count"))
    aired = _air_date(item.get("originally_available_at")) if media_type == "episode" else None
    return join([
        item.get("content_rating"),
        runtime(item.get("duration")) if media_type in ("movie", "episode") else None,
        plural(seasons, "season") if media_type == "show" and seasons else None,
        f"aired {aired}" if aired else None,
        ", ".join(_genre(g) for g in (item.get("genres") or [])[:3]),
        _star_rating(item),
    ])


def _credits(item: dict) -> Optional[str]:
    directors, actors = _names(item.get("directors"), 2), _names(item.get("actors"), _CREDITS)
    return join([
        f"Directed by {', '.join(directors)}" if directors else None,
        f"Starring {', '.join(actors)}" if actors else None,
    ]) or None


def media_card(item: dict, *, poster: Optional[Attachment] = None, art: Optional[Attachment] = None,
               url: Optional[str] = None, stats: Optional[str] = None) -> nextcord.Embed:
    """A movie, show, season or episode (Tautulli metadata or a media cache item).

    Tinted with the poster's colour when there is one; `stats` is an extra line for watch stats.
    """
    media_type = item.get("media_type")
    facts, credit_line = media_facts(item), _credits(item)
    tagline = item.get("tagline")
    summary = truncate(item.get("summary") or "", _SUMMARY_LENGTH)
    description = join([
        f"-# {facts}" if facts else None,
        f"*{escape_markdown(tagline)}*" if tagline else None,
        escape_markdown(summary) if summary else None,
        f"-# {escape_markdown(credit_line)}" if credit_line else None,
        f"\n{stats}" if stats else None,
    ], "\n")
    colour = (dominant_colour(poster.data) if poster else None) or media_colour(media_type)
    embed = card(title=session_title(item), description=description or None, colour=colour, url=url)
    embed.set_author(name=f"{media_icon(media_type)} {_MEDIA_NAMES.get(media_type or '', 'Media')}")
    set_thumbnail(embed, poster)
    set_image(embed, art)
    return embed
