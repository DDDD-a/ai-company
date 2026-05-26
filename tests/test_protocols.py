"""测试通信协议层"""
import pytest
from protocols.verbs import Event, Verb
from protocols.parser import parse_compact, extract_mentions


class TestEvent:
    def test_creation(self):
        e = Event(verb=Verb.UPD, agent="BE_1", task="#t1", status="50%")
        assert e.id != ""
        assert len(e.id) == 36  # UUID
        assert e.timestamp > 0
        assert e.verb == Verb.UPD

    def test_to_compact_basic(self):
        e = Event(verb=Verb.UPD, agent="BE_1", task="#auth_api", status="50%",
                  payload={"impl": "jwt"})
        compact = e.to_compact()
        assert "UPD" in compact
        assert "BE_1" in compact
        assert "#auth_api" in compact
        assert "50%" in compact

    def test_to_compact_with_mentions(self):
        e = Event(verb=Verb.ASN, agent="HR", task="#t1", status="P1",
                  mentions=["BE", "QA"])
        compact = e.to_compact()
        assert "@BE" in compact
        assert "@QA" in compact

    def test_mentions_agent(self):
        e = Event(verb=Verb.ASN, agent="HR", task="#t1",
                  mentions=["BE", "QA"])
        assert e.mentions_agent("BE")
        assert e.mentions_agent("QA")
        assert not e.mentions_agent("FE")

    def test_all_verbs(self):
        for verb in Verb:
            e = Event(verb=verb, agent="test", task="#t")
            assert e.verb == verb


class TestParser:
    def test_roundtrip(self):
        e = Event(verb=Verb.UPD, agent="BE_1", task="#auth_api", status="50%",
                  payload={"impl": "jwt"})
        parsed = parse_compact(e.to_compact())
        assert parsed is not None
        assert parsed.verb == Verb.UPD
        assert parsed.agent == "BE_1"
        assert parsed.task == "#auth_api"

    def test_roundtrip_with_mentions(self):
        e = Event(verb=Verb.ASN, agent="HR", task="#t1",
                  mentions=["BE", "QA"])
        parsed = parse_compact(e.to_compact())
        assert "BE" in parsed.mentions
        assert "QA" in parsed.mentions

    def test_parse_invalid(self):
        assert parse_compact("") is None
        assert parse_compact("INVALID") is None
        assert parse_compact("NOT_A_VERB|agent|task") is None

    def test_extract_mentions(self):
        text = "ASN|HR|#t1|P1|agent:BE @BE @QA"
        mentions = extract_mentions(text)
        assert "BE" in mentions
        assert "QA" in mentions

    def test_roundtrip_all_verbs(self):
        for verb in Verb:
            e = Event(verb=verb, agent="test", task="#t",
                      payload={"k": "v"}, mentions=["X"])
            parsed = parse_compact(e.to_compact())
            assert parsed is not None
            assert parsed.verb == verb
