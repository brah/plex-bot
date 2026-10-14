"""Guards for our nextcord UI classes (views, modals, selects).

nextcord calls many methods on these classes itself, so a subclass method that
reuses one of their names silently replaces it. WatchersView.refresh() did exactly
that: nextcord calls View.refresh(components) whenever a message with buttons is
edited, got ours (no arguments) instead, and the TypeError took down the bot.
"""

import asyncio
import importlib
import inspect
import pkgutil

import nextcord
import pytest

import cogs
import errors
from cogs.server_commands import ServerCommands, WatchersView
from plexbot import PlexBot

# Methods nextcord documents as meant to be overridden.
OVERRIDABLE_HOOKS = {"callback", "interaction_check", "on_timeout", "on_error"}
UI_BASES = (nextcord.ui.View, nextcord.ui.Modal, nextcord.ui.Item)


def _our_ui_classes():
    modules = [errors] + [
        importlib.import_module(f"cogs.{info.name}") for info in pkgutil.iter_modules(cogs.__path__)
    ]
    found = []
    for module in modules:
        for obj in vars(module).values():
            if inspect.isclass(obj) and issubclass(obj, UI_BASES) and obj.__module__ == module.__name__:
                found.append(obj)
    return found


def test_finds_our_ui_classes():
    names = {cls.__name__ for cls in _our_ui_classes()}
    expected = {"WatchersView", "StreamDetailsSelect", "StreamDetailView", "StopStreamModal", "LoggedView"}
    assert expected <= names


@pytest.mark.parametrize("cls", _our_ui_classes(), ids=lambda cls: cls.__name__)
def test_ui_class_does_not_replace_nextcord_methods(cls):
    nextcord_bases = [base for base in cls.__mro__ if base.__module__.startswith("nextcord")]
    for name in vars(cls):
        if name.startswith("__") or name in OVERRIDABLE_HOOKS:
            continue
        clashes = [base.__name__ for base in nextcord_bases if name in vars(base)]
        assert not clashes, f"{cls.__name__}.{name} replaces nextcord's {clashes[0]}.{name}"


def test_editing_a_watchers_message_does_not_crash():
    """The crash path: nextcord re-syncs a tracked view when its message is edited."""

    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None)
        view = WatchersView(object.__new__(ServerCommands), [])
        bot._connection.store_view(view, 5555)
        assert bot._connection._view_store.is_message_tracked(5555)
        # What parse_message_update does with the components Discord echoes back.
        bot._connection._view_store.update_from_message(5555, view.to_components())
        assert [child.label for child in view.children] == ["Refresh"]

    asyncio.run(run())
