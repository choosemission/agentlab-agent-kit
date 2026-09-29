"""M3 tooling: capture what the gateway delivered, then read it back with `verify`.

The capture is the evidence behind every re-measured claim, so the tests check
that it is faithful (the body as it arrived), that it never holds a secret, and
that it records only what got past the key gate.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from labagent.owner.cli import main as cli
from labagent.trust.evidence import examine
from labagent.trust.resolve import Resolver
from labagent.trust.testing import fetcher_for

from .conftest import BOB_KEY, make_agent
from .test_doors import SEND, call
from .test_ping import ping


def capturing_bob(lab, directory: Path):
    bob = make_agent(replace(lab.bob.ctx.config, capture_dir=str(directory)), lab.bob.ctx.peers, lab)
    lab.bob = bob
    lab.network.apps["bob.test"] = bob.public_app
    return bob


def captures(directory: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def offline(lab):
    return lambda strict: Resolver(fetcher=fetcher_for(lab.alice_gw, lab.bob_gw), strict_purpose=strict)


def test_a_delivered_message_is_captured_as_it_arrived(make_lab, tmp_path) -> None:
    lab = make_lab()
    capturing_bob(lab, tmp_path / "cap")
    assert ping(lab, "alice", "bob")["reply"]["seen_as"] == "gateway-verified"

    [record] = [r for r in captures(tmp_path / "cap") if r["method"] == "POST"]
    assert record["headers"]["x-api-key"] == "<present>"
    raw = (tmp_path / "cap").glob("*.json")
    assert not any(BOB_KEY in p.read_text() for p in raw), "a credential value was written"
    # The body is kept as text, so a serialised presentation stays serialised.
    assert isinstance(json.loads(record["body"])["params"]["message"]["metadata"], dict)


def test_verify_reads_the_capture(make_lab, tmp_path) -> None:
    lab = make_lab()
    capturing_bob(lab, tmp_path / "cap")
    ping(lab, "alice", "bob")
    [record] = [r for r in captures(tmp_path / "cap") if r["method"] == "POST"]

    report = examine(record, resolver=offline(lab))
    assert report["verified"] is True
    assert report["proofs"]["issuer"] == lab.alice_gw.gateway_did
    assert report["presentation"]["serialised_as"] == "JSON string"
    assert report["presentation"]["extension"].endswith("agent-identity-binding/v1")
    assert report["credential_headers"] == {"x-api-key": True, "authorization": False}


def test_verify_says_so_when_nothing_was_signed(make_lab, tmp_path) -> None:
    lab = make_lab(alice_signs=False)
    capturing_bob(lab, tmp_path / "cap")
    ping(lab, "alice", "bob")
    [record] = [r for r in captures(tmp_path / "cap") if r["method"] == "POST"]
    report = examine(record, resolver=offline(lab))
    assert report["verified"] is False and report["presentation"] is None


def test_the_card_is_captured_and_the_health_check_is_not(make_lab, tmp_path) -> None:
    lab = make_lab()
    bob = capturing_bob(lab, tmp_path / "cap")
    assert call(bob.public_app, "GET", "/.well-known/agent-card.json").status_code == 200
    assert call(bob.public_app, "GET", "/healthz").status_code == 200
    paths = [r["path"] for r in captures(tmp_path / "cap")]
    assert paths == ["/.well-known/agent-card.json"]


def test_nothing_refused_by_the_gate_is_captured(make_lab, tmp_path) -> None:
    lab = make_lab()
    bob = capturing_bob(lab, tmp_path / "cap")
    assert call(bob.public_app, "POST", "/", json=SEND).status_code == 401
    assert captures(tmp_path / "cap") == []


def test_the_cli_exits_non_zero_when_nothing_verifies(tmp_path, capsys) -> None:
    path = tmp_path / "message.json"
    path.write_text(json.dumps(SEND))
    try:
        cli(["verify", str(path)])
    except SystemExit as exit:
        assert exit.code == 1
    else:
        raise AssertionError("verify accepted an unsigned message")
    assert '"presentation": null' in capsys.readouterr().out

