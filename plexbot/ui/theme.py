"""Colour roles, Discord limits and the small emoji set shared by every response."""

from plexbot.config import config


class Colour:
    """Colour roles. Cogs pick a role, never a literal value."""

    PLAYING = 0x3BA55C
    SUCCESS = PLAYING
    WARNING = 0xF0A93B
    ERROR = 0xED4245
    NEUTRAL = 0x5D6572
    MOVIE = 0xE5A00D
    TV = 0x4A90D9
    MUSIC = 0xEB459E
    GOLD = 0xF1C40F
    SILVER = 0xBFC5CC
    BRONZE = 0xCD7F32


_MEDIA_COLOURS = {
    "movie": Colour.MOVIE,
    "show": Colour.TV,
    "season": Colour.TV,
    "episode": Colour.TV,
    "artist": Colour.MUSIC,
    "album": Colour.MUSIC,
    "track": Colour.MUSIC,
}


def brand_colour() -> int:
    """The neutral brand accent (config ui.plex_embed_color), for frames with no state.

    JSON has no hex numbers, so hex text ("#E5A00D", "0xE5A00D") is accepted too; anything else
    falls back to Plex orange rather than breaking every embed.
    """
    value = config.get("ui", "plex_embed_color")
    if isinstance(value, str):
        try:
            value = int(value.strip().lstrip("#"), 16)
        except ValueError:
            return Colour.MOVIE
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFF:
        return Colour.MOVIE
    return value


def media_colour(media_type) -> int:
    return _MEDIA_COLOURS.get(media_type or "", Colour.NEUTRAL)


class Limits:
    """Discord's hard limits; exceeding one makes Discord reject the whole message."""

    TITLE = 256
    DESCRIPTION = 4096
    FIELDS = 25
    FIELD_NAME = 256
    FIELD_VALUE = 1024
    FOOTER = 2048
    AUTHOR = 256
    EMBEDS_PER_MESSAGE = 10
    SELECT_OPTIONS = 25
    SELECT_LABEL = 100


class Emoji:
    PLAYING = "▶"
    PAUSED = "⏸"
    BUFFERING = "⏳"
    BULLET = "•"
    PLEX = "Plex Media Server"  # becomes the bot's :plex: emoji once bot.install_plex_emoji finds or uploads it
