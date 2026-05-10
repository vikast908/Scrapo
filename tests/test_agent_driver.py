from scrapo.access.agent_drivers import LLMAgentDriver, _format_elements, parse_action
from scrapo.access.router import TierRouter
from scrapo.config import Config
from scrapo.extract.llm_adapters.base import LLMResponse


def _resp(text="", payload=None):
    return LLMResponse(text=text, json_payload=payload, provider="x", model_id="x-1")


def test_parse_action_from_json_payload():
    assert parse_action(_resp(payload={"action": "click", "target": 3, "reason": "the button"})) == {
        "action": "click",
        "target": 3,
        "text": None,
        "reason": "the button",
    }


def test_parse_action_from_fenced_text():
    a = parse_action(_resp(text='```json\n{"action": "type", "target": 1, "text": "hi"}\n```'))
    assert a["action"] == "type"
    assert a["target"] == 1
    assert a["text"] == "hi"


def test_parse_action_unknown_or_garbage_is_done():
    assert parse_action(_resp(payload={"action": "teleport"}))["action"] == "done"
    assert parse_action(_resp(text="not json"))["action"] == "done"
    assert parse_action(_resp())["action"] == "done"


def test_parse_action_coerces_bad_fields():
    a = parse_action(_resp(payload={"action": "click", "target": "five", "text": 42}))
    assert a["target"] is None
    assert a["text"] is None


def test_format_elements():
    s = _format_elements(
        [{"id": 0, "tag": "button", "type": "", "text": "Submit"}, {"id": 1, "tag": "input", "type": "text", "text": ""}]
    )
    assert '0: button "Submit"' in s
    assert '1: input[text] ""' in s
    assert _format_elements([]) == "(none found)"


def test_router_wires_agent_driver_when_configured(tmp_path):
    router = TierRouter(Config(data_dir=tmp_path / "a", agent_driver="llm"))
    assert isinstance(router.agent.driver, LLMAgentDriver)
    assert TierRouter(Config(data_dir=tmp_path / "b")).agent.driver is None
