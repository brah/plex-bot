"""Shared test fakes: Tautulli session payloads (and, from Task 5, dispatch helpers)."""

MOVIE_PLAYING = {
    "session_id": "abc", "session_key": "1", "media_type": "movie", "state": "playing",
    "friendly_name": "Alice", "username": "alice", "user_thumb": "https://plex.tv/users/1/avatar",
    "title": "The Matrix", "full_title": "The Matrix", "year": "1999", "rating_key": "101",
    "thumb": "/library/metadata/101/thumb/1", "art": "/library/metadata/101/art/1",
    "bif_thumb": "/library/parts/55/indexes/sd/600000", "indexes": 1,
    "view_offset": "600000", "duration": "8160000",
    "transcode_decision": "direct play", "video_decision": "direct play", "audio_decision": "direct play",
    "video_full_resolution": "1080p", "stream_video_full_resolution": "1080p", "video_codec": "h264",
    "audio_codec": "eac3", "audio_channel_layout": "5.1(side)",
    "player": "Living Room", "product": "Plex for Roku", "platform": "Roku",
    "location": "lan", "bandwidth": "12000",
}

EPISODE_PAUSED = {
    "session_id": "def", "session_key": "2", "media_type": "episode", "state": "paused",
    "friendly_name": "Bob", "username": "bob", "user_thumb": "",
    "title": "Hello, Ms. Cobel", "grandparent_title": "Severance", "parent_media_index": "1",
    "media_index": "2", "year": "2022", "rating_key": "202",
    "thumb": "/library/metadata/202/thumb/1", "grandparent_thumb": "/library/metadata/200/thumb/1",
    "art": "/library/metadata/200/art/1", "bif_thumb": "", "indexes": 0,
    "view_offset": "100000", "duration": "3000000",
    "transcode_decision": "transcode", "video_decision": "transcode", "audio_decision": "copy",
    "video_full_resolution": "4k", "stream_video_full_resolution": "1080p", "video_codec": "hevc",
    "audio_codec": "truehd", "audio_channel_layout": "7.1",
    "subtitle_codec": "srt", "subtitle_language": "English", "subtitle_decision": "burn",
    "player": "Bob's iPhone", "product": "Plex for iOS", "platform": "iOS",
    "location": "wan", "bandwidth": "8000",
}

TRACK_PLAYING = {
    "session_id": "ghi", "media_type": "track", "state": "playing", "friendly_name": "Cara",
    "username": "cara", "title": "Bye Bye Bye", "grandparent_title": "*NSYNC",
    "parent_thumb": "/library/metadata/300/thumb/1", "thumb": "/library/metadata/301/thumb/1",
    "view_offset": "60000", "duration": "200000", "transcode_decision": "direct play",
    "audio_codec": "flac", "audio_channel_layout": "stereo", "player": "Desktop", "location": "lan",
}

LIVE_TV = {
    "session_id": "jkl", "media_type": "clip", "state": "playing", "username": "dan",
    "title": "News at Ten", "view_offset": "0", "duration": "0", "thumb": "",
    "transcode_decision": "", "location": "wan",
}

MARKDOWN_TITLE = dict(MOVIE_PLAYING, session_id="mno", friendly_name="_eve_", title="**Bold** _Live_")


def many_sessions(n: int) -> list:
    return [
        dict(MOVIE_PLAYING, session_id=f"s{i}", friendly_name=f"User {i}", username=f"user{i}",
             title=f"Film {i}")
        for i in range(n)
    ]
