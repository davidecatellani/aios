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
    answer = agent.ask("installa vlc")

    assert asked == [("install_app", {"app_id": "org.videolan.VLC", "source": "flatpak"})]
    assert log == []
    # ci si ferma subito: niente commento del modello («L'utente ha rifiutato…») in terza persona
    assert answer == "Va bene, non lo faccio." and len(model.seen) == 1


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


def test_nova_does_not_introduce_itself_every_time():
    from aios_copilot.agent import strip_intro

    assert strip_intro("Ciao! Sono Nova, il tuo assistente. Domani piove.", "che tempo fa domani") == "Domani piove."
    assert strip_intro("Ciao Davide! Ecco i file.", "trova i file") == "Ecco i file."
    assert strip_intro("Mi chiamo Nova.", "come ti chiami?") == "Mi chiamo Nova."
    assert strip_intro("Sono Nova, l'assistente di AIOS.", "chi è Nova?") == "Sono Nova, l'assistente di AIOS."
    assert strip_intro("Ciao! Come stai?", "ciao") == "Ciao! Come stai?"
    assert strip_intro("Il sistema è aggiornato.", "aggiorna") == "Il sistema è aggiornato."


def test_dead_end_shortcut_goes_to_the_model():
    from aios_copilot.agent import Intent

    class Shortcut:
        def match(self, text):
            return Intent("search_web", {"query": text})

    tools = [Tool("search_web", "Cerca", {"type": "object", "properties": {"query": {"type": "string"}}},
                  lambda query: "Nessun risultato.")]
    model = ScriptedModel([{"content": "Ti consiglio Dark e The Bear."}])
    agent = Agent(model, tools, confirm=lambda *a: True, routers=[Shortcut()])
    assert agent.ask("mi consigli una serie tv?") == "Ti consiglio Dark e The Bear."
    sent = model.seen[0]
    assert "Nessun risultato" in sent[-1]["content"] and "non per l'utente" in sent[-1]["content"]


def test_laya_route_skips_shortcuts_for_questions():
    from aios_copilot.agent import Intent

    class Shortcut:
        def match(self, text):
            return Intent("search_web", {"query": text})

    used = []
    tools = [Tool("search_web", "Cerca", {"type": "object", "properties": {"query": {"type": "string"}}},
                  lambda query: used.append(query) or "catalogo")]
    model = ScriptedModel([{"content": "Prova Hollow Knight o Stardew Valley."}])
    agent = Agent(model, tools, confirm=lambda *a: True, routers=[Shortcut()], percorso=lambda t: ("risposta", 0.9))
    assert agent.ask("che giochi mi proponi?") == "Prova Hollow Knight o Stardew Valley." and used == []
