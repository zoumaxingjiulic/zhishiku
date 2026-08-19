"""Run inside the API container to verify configured embedding and chat models."""

from app.retrieval import embedding, generate_answer


vector = embedding("企业知识库向量接口连通性测试")
if not vector:
    raise RuntimeError("Embedding returned no vector")
print(f"EMBEDDING_OK dimension={len(vector)}")

answer, method = generate_answer(
    "只根据资料回答",
    "年休假需提前多久申请？",
    [{"title": "测试制度", "page_start": 1, "content_text": "年休假须提前三个工作日申请。"}],
)
if method != "llm" or not answer:
    raise RuntimeError(f"Chat model was not used: {method}")
print(f"LLM_OK method={method} answer_chars={len(answer)}")
