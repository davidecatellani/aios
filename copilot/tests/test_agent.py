from aios_copilot.agent import Agent
from aios_copilot.tools import Tool
from aios_copilot.tools.base import params


class ScriptedModel:
    """Modello finto che restituisce risposte prestabilite e registra le richieste."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def make_tools(log):
    return [
        Tool("search_web", "cerca", params(query="q"), lambda query: log.append(("search", query)) or "risultato: 42"),
        Tool(
            "install_app",
            "installa",
            params(app_id="id", source="s"),
            lambda app_id, source: log.append(("install", app_id)) or f"Installato {app_id}.",
            requires_confirmation=True,
        ),
    ]


def test_tool_result_is_sent_back_to_model():
    log = []
    model = ScriptedModel([call("search_web", query="meteo"), {"content": "La risposta è 42."}])
    agent = Agent(model, make_tools(log), confirm=lambda t, a: True)

    assert agent.ask("che tempo fa?") == "La risposta è 42."
    assert log == [("search", "meteo")]
    tool_msg = model.seen[1][-1]
    assert tool_msg == {"role": "tool", "tool_name": "search_web", "content": "risultato: 42"}


def test_confirmation_required_and_refusal_respected():
    log, asked = [], []
    model = ScriptedModel([call("install_app", app_id="org.videolan.VLC", source="flatpak"), {"content": "Ok, non installo."}])

    def refuse(tool, args):
        asked.append((tool.name, args))
        return False

    agent = Agent(model, make_tools(log), confirm=refuse)
    agent.ask("installa vlc")

    assert asked == [("install_app", {"app_id": "org.videolan.VLC", "source": "flatpak"})]
    assert log == []
    assert "rifiutato" in model.seen[1][-1]["content"]


def test_confirmation_accepted_runs_tool():
    log = []
    model = ScriptedModel([call("install_app", app_id="org.videolan.VLC", source="flatpak"), {"content": "Fatto."}])
    Agent(model, make_tools(log), confirm=lambda t, a: True).ask("installa vlc")
    assert log == [("install", "org.videolan.VLC")]


def test_string_arguments_unknown_tools_and_bad_args():
    log = []
    model = ScriptedModel(
        [
            {"content": "", "tool_calls": [
                {"function": {"name": "search_web", "arguments": '{"query": "treni"}'}},
                {"function": {"name": "format_disk", "arguments": {}}},
                {"function": {"name": "search_web", "arguments": {"wrong": 1}}},
            ]},
            {"content": "fine"},
        ]
    )
    Agent(model, make_tools(log), confirm=lambda t, a: True).ask("x")
    results = [m["content"] for m in model.seen[1] if m["role"] == "tool"]
    assert results[0] == "risultato: 42"
    assert "sconosciuto" in results[1]
    assert "Argomenti errati" in results[2]


def test_step_limit():
    model = ScriptedModel([call("search_web", query="a")] * 3)
    agent = Agent(model, make_tools([]), confirm=lambda t, a: True, max_steps=3)
    assert "troppi passaggi" in agent.ask("loop")


def test_conversation_keeps_context():
    model = ScriptedModel([{"content": "Ciao!"}, {"content": "Certo."}])
    agent = Agent(model, [], confirm=lambda t, a: True)
    agent.ask("ciao")
    agent.ask("e ora?")
    roles = [m["role"] for m in model.seen[1]]
    assert roles == ["system", "user", "assistant", "user"]
