import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "music-assistant-alexa-api"
    / "skill_patches"
    / "account_routing.py"
)
spec = importlib.util.spec_from_file_location("account_routing", MODULE)
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

A, B, C = "spotify--D4RBmVqJ", "spotify--bY7NWf7o", "spotify--awsWr68c"
CONFIG = {"players": {"Kitchen": [A, B, C], "Olive": [B, A, C]}, "default": [A, B, C]}


class FakeClient:
    def __init__(self, queues=None, fail=False):
        self.queues = queues or []
        self.fail = fail

    async def send_command(self, command):
        assert command == "player_queues/all"
        if self.fail:
            raise RuntimeError("boom")
        return self.queues


def playing(queue_id, uri, state="playing"):
    return {"queue_id": queue_id, "state": state, "current_item": {"media_item": {"uri": uri}}}


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setenv("ACCOUNT_ROUTING", json.dumps(CONFIG))


def run(coro):
    return asyncio.run(coro)


def test_rewrite_only_touches_spotify():
    assert ar.rewrite_uri(C + "://track/abc", A) == A + "://track/abc"
    assert ar.rewrite_uri("spotify://track/abc", A) == "spotify://track/abc"
    assert ar.rewrite_uri("library://track/12", A) == "library://track/12"
    assert ar.rewrite_uri(None, A) is None


def test_instances_for_falls_back_and_ignores_case():
    assert ar.instances_for(CONFIG, "olive") == [B, A, C]
    assert ar.instances_for(CONFIG, "Basement") == [A, B, C]
    assert ar.instances_for({}, "Kitchen") == []


def test_first_free_account_is_used(routing):
    client = FakeClient([playing("Olive", A + "://track/x")])
    assert run(ar.route(client, "Kitchen", C + "://track/abc")) == B + "://track/abc"


def test_own_queue_and_paused_queues_do_not_count(routing):
    client = FakeClient(
        [playing("Kitchen", A + "://track/x"), playing("Olive", A + "://track/y", "paused")]
    )
    assert run(ar.route(client, "Kitchen", C + "://track/abc")) == A + "://track/abc"


def test_all_busy_uses_first(routing):
    client = FakeClient(
        [playing("Olive", A + "://t/1"), playing("Vivian", B + "://t/2"), playing("Hot tub", C + "://t/3")]
    )
    assert run(ar.route(client, "Kitchen", C + "://track/abc")) == A + "://track/abc"


def test_lists_are_rewritten(routing):
    media = [C + "://track/1", "library://track/2"]
    out = run(ar.route(FakeClient(), "Olive", media))
    assert out == [B + "://track/1", "library://track/2"]


def test_non_spotify_media_skips_queue_lookup(routing):
    client = FakeClient(fail=True)
    assert run(ar.route(client, "Kitchen", "library://album/3")) == "library://album/3"


def test_queue_lookup_failure_still_routes(routing):
    assert run(ar.route(FakeClient(fail=True), "Kitchen", C + "://t/1")) == A + "://t/1"


def test_unconfigured_passes_through(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCOUNT_ROUTING", raising=False)
    monkeypatch.setenv("ACCOUNT_ROUTING_OPTIONS_PATH", str(tmp_path / "missing.json"))
    assert run(ar.route(FakeClient(fail=True), "Kitchen", C + "://t/1")) == C + "://t/1"


def test_reads_addon_options(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCOUNT_ROUTING", raising=False)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"account_routing": json.dumps(CONFIG)}))
    monkeypatch.setenv("ACCOUNT_ROUTING_OPTIONS_PATH", str(options))
    assert run(ar.route(FakeClient(), "Olive", C + "://t/1")) == B + "://t/1"


def test_invalid_json_disables_routing(monkeypatch):
    monkeypatch.setenv("ACCOUNT_ROUTING", "{not json")
    assert ar.load_config() == {}
