from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider


def test_memory_retrieval_and_forget(tmp_path):
    store = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    mem_id = store.add("Greek coffee in Athens", source="session:test", importance=0.8)
    store.add("Kernel command output", source="tool:test", importance=0.2)
    results = store.search("Athens coffee", top_k=1)
    assert results[0].id == mem_id
    assert store.forget(mem_id)
    assert all(item.id != mem_id for item in store.items)
