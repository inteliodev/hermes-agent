"""The front door loads one client's pack and hands the result back."""

from intelio.coordinator import front_door_text, handoff
from intelio.knowledge import pack_token


def test_one_client_routes_to_one_specialist(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = handoff("What is the PRC sop for logging a request?")
    assert result["ok"] is True
    assert result["client"] == "PRC"
    assert result["specialist"] == "sops"
    assert result["for"] == "Hayden"
    assert pack_token("PRC") in result["context"]
    assert "2753" in result["context"]
    assert pack_token("intelio") not in result["context"]
    assert "1842" not in result["context"]
    assert result["result"] == "Hand back to Hayden: PRC / sops. Not sent."

    policy = handoff("Which Alignment policy is forbidden?")
    assert policy["client"] == "Alignment"
    assert policy["specialist"] == "policies"
    contacts = handoff("Who handles HHP phone questions?")
    assert contacts["specialist"] == "contacts"
    template = handoff("Show the ARLP status note template")
    assert template["specialist"] == "templates"
    decision = handoff("How does intelio decide an approval?")
    assert decision["client"] == "intelio"
    assert decision["specialist"] == "decisions"


def test_two_clients_are_refused_and_no_pack_is_loaded():
    result = handoff("Compare the PRC and HHP sops")
    assert result["ok"] is False
    assert result["context"] == ""
    assert result["error"] == "name one client; refusing to mix clients"
    assert result["result"] == "Hold for Hayden. No client pack loaded."


def test_no_client_holds_for_the_owner():
    result = handoff("What should I do next?", owner="Hayden")
    assert result["ok"] is True
    assert result["client"] is None
    assert result["context"] == ""
    assert result["result"] == "Hold for Hayden. No client pack loaded."
    text = front_door_text("Hayden")
    assert "Hayden" in text
    assert "PRC" in text and "ARLP" in text
    assert "Do not send messages." in text
