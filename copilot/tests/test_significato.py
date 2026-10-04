from aios_copilot.fileindex import FileIndex
from aios_copilot.learning import MeaningModelTask
from aios_copilot.semantic import prefixes_for


def test_prefixes():
    sim, query, doc = prefixes_for("embeddinggemma:latest")
    assert query.startswith("task: search result") and doc.startswith("title: none")
    assert prefixes_for("granite-embedding:278m") == ("", "", "")


def test_vectors_reset_when_model_changes(tmp_path):
    idx = FileIndex(db_path=tmp_path / "i.db", roots=[tmp_path])
    idx.db.execute("INSERT INTO chunks (path, n, text) VALUES ('a', 0, 'ciao')")
    idx.vectors_for("granite-embedding:278m")
    cid = idx.chunks_without_vectors(1)[0][0]
    idx.store_vectors([(cid, [1.0, 0.0])])
    idx.vectors_for("granite-embedding:278m")
    assert idx.chunks_without_vectors(1) == []  # stesso modello: si tengono
    idx.vectors_for("embeddinggemma")
    assert idx.chunks_without_vectors(1) == [(cid, "ciao")]  # modello nuovo: si rifanno


def test_bundled_meaning_model_activates_once():
    done = []
    task = MeaningModelTask(installed=lambda: {"embeddinggemma", "qwen3.5:2b"},
                            activate=lambda name, cap: done.append((name, cap)))
    assert task.has_work()
    task.step(1)
    assert done == [("embeddinggemma", "significato")]
    assert not MeaningModelTask(installed=lambda: {"qwen3.5:2b"}).has_work()


def test_glm_ocr_gets_its_own_prompt(tmp_path):
    from aios_copilot import engines

    img = tmp_path / "bolletta.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    seen = []

    def chat(path, payload):
        seen.append(payload["messages"][0]["content"])
        return {"message": {"content": "Totale 12,30"}}

    assert engines.read_document(img, "glm-ocr:q8_0", chat) == "Totale 12,30"
    assert seen == ["Text Recognition:"]
    engines.read_document(img, "deepseek-ocr:3b", chat)
    assert seen[-1] == engines.READ_DOCUMENT
