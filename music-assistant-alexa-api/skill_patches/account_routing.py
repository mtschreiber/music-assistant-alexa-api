# -*- coding: utf-8 -*-
"""Pick which Spotify account plays on each Echo.

Music Assistant merges every Spotify account into one library and picks the
streaming account itself. A Spotify Family member can only stream to one
device at a time, so two Echos started from the same account stop each other.

This module rewrites a resolved Spotify URI such as
``spotify--awsWr68c://track/<id>`` to the provider instance configured for the
player that will play it. The Spotify item id is the same in every account,
only the ``spotify--<instance>`` prefix changes.

Configuration comes from the add-on option ``account_routing``, a JSON string:

    {
      "players": {
        "Kitchen": ["spotify--D4RBmVqJ", "spotify--bY7NWf7o", "spotify--awsWr68c"],
        "Olive":   ["spotify--bY7NWf7o", "spotify--D4RBmVqJ", "spotify--awsWr68c"]
      },
      "default": ["spotify--D4RBmVqJ", "spotify--bY7NWf7o", "spotify--awsWr68c"]
    }

For a player the first instance that is not already streaming on another
player is used. When every listed instance is busy the first one is used, which
stops the other player, the same as Spotify itself would. Library URIs
(library://track/2) are first resolved to the item's Spotify mapping; URIs
from any other source (radio, local files) are never changed. With no
configuration every URI passes through unchanged.
"""

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

OPTIONS_PATH = "/data/options.json"
OPTION_KEY = "account_routing"
_INSTANCE = re.compile(r"(spotify--[A-Za-z0-9]+)://")


def _options_path():
    return os.environ.get("ACCOUNT_ROUTING_OPTIONS_PATH", OPTIONS_PATH)


def load_config():
    """Return the routing config dict, or {} when unset or invalid."""
    raw = os.environ.get("ACCOUNT_ROUTING")
    if raw is None:
        try:
            with open(_options_path(), "r", encoding="utf-8") as f:
                raw = json.load(f).get(OPTION_KEY)
        except FileNotFoundError:
            return {}
        except Exception:
            logger.exception("Could not read %s", _options_path())
            return {}
    if not raw:
        return {}
    try:
        config = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        logger.error("account_routing is not valid JSON; routing disabled")
        return {}
    if not isinstance(config, dict):
        logger.error("account_routing must be a JSON object; routing disabled")
        return {}
    return config


def instances_for(config, player_id):
    """Ordered provider instances for a player (exact, then case-insensitive)."""
    players = config.get("players") or {}
    order = players.get(player_id)
    if order is None and player_id:
        wanted = str(player_id).casefold()
        order = next(
            (value for key, value in players.items() if str(key).casefold() == wanted),
            None,
        )
    if order is None:
        order = config.get("default") or []
    return [str(item) for item in order if item]


def instance_of(uri):
    match = _INSTANCE.match(uri) if isinstance(uri, str) else None
    return match.group(1) if match else None


def rewrite_uri(uri, instance):
    """Swap the Spotify provider instance of a URI; leave anything else alone."""
    match = _INSTANCE.match(uri) if isinstance(uri, str) else None
    if not match or not instance:
        return uri
    return instance + uri[match.end(1):]


def _as_dict(item):
    if isinstance(item, dict):
        return item
    to_dict = getattr(item, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


def _streaming_instance(current):
    """The Spotify account a queue item is streaming from, if any."""
    details = current.get("streamdetails") or {}
    provider = str(details.get("provider") or "")
    if provider.startswith("spotify--"):
        return provider
    media = current.get("media_item") or {}
    found = instance_of(str(media.get("uri") or "")) or instance_of(
        str(current.get("uri") or "")
    )
    if found:
        return found
    # Library items carry every account in provider_mappings; only trust a
    # single Spotify mapping, never guess between several.
    spotify = {
        str(m.get("provider_instance"))
        for m in media.get("provider_mappings") or []
        if str(m.get("provider_instance", "")).startswith("spotify--")
    }
    return spotify.pop() if len(spotify) == 1 else None


def busy_from_queues(queues, player_id):
    """Spotify instances currently streaming on any player other than this one."""
    busy = set()
    for queue in queues or []:
        queue = _as_dict(queue)
        if queue.get("queue_id") == player_id:
            continue
        if str(queue.get("state", "")).lower() != "playing":
            continue
        instance = _streaming_instance(queue.get("current_item") or {})
        if instance:
            busy.add(instance)
    return busy


def choose_instance(order, busy):
    if not order:
        return None
    return next((item for item in order if item not in busy), order[0])


async def busy_instances(client, player_id):
    try:
        queues = await client.send_command("player_queues/all")
    except Exception as exc:  # pragma: no cover - logged and tolerated
        logger.warning("Could not read queues for account routing: %s", exc)
        return set()
    return busy_from_queues(queues, player_id)


_LIBRARY = re.compile(r"library://([a-z_]+)/(.+)$")


def spotify_uri_for(item, media_type):
    """A spotify--<instance>://<type>/<id> URI from a library item's mappings."""
    for mapping in _as_dict(item).get("provider_mappings") or []:
        mapping = _as_dict(mapping)
        instance = str(mapping.get("provider_instance") or "")
        if instance.startswith("spotify--") and mapping.get("item_id"):
            if mapping.get("available", True) is False:
                continue
            return f"{instance}://{media_type}/{mapping['item_id']}"
    return None


async def resolve_library(client, uri):
    """Turn a library:// URI into a Spotify URI; leave it alone when impossible."""
    match = _LIBRARY.match(uri) if isinstance(uri, str) else None
    if not match:
        return uri
    try:
        item = await client.send_command("music/item_by_uri", uri=uri)
    except Exception as exc:
        logger.warning("Could not look up %s for account routing: %s", uri, exc)
        return uri
    return spotify_uri_for(item, match.group(1)) or uri


async def route(client, player_id, media):
    """Return media (a URI or list of URIs) rewritten to the player's account."""
    config = load_config()
    if not config:
        return media
    order = instances_for(config, player_id)
    if not order:
        return media
    uris = media if isinstance(media, list) else [media]
    if any(_LIBRARY.match(uri) for uri in uris if isinstance(uri, str)):
        uris = list(await asyncio.gather(*(resolve_library(client, u) for u in uris)))
    if not any(instance_of(uri) for uri in uris):
        logger.info("Account routing: %s has no Spotify media to route", player_id)
        return media
    busy = await busy_instances(client, player_id)
    chosen = choose_instance(order, busy)
    if chosen in busy:
        logger.warning(
            "Every Spotify account for %s is busy; using %s anyway", player_id, chosen
        )
    logger.info("Account routing: %s -> %s (busy: %s)", player_id, chosen, sorted(busy))
    routed = [rewrite_uri(uri, chosen) for uri in uris]
    return routed if isinstance(media, list) else routed[0]
