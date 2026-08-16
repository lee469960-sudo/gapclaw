# RAG 知识库

## 能力

- 上传 txt/md/csv/json/pdf/docx，切块索引后可检索
- Agent 可绑定知识库，对话中通过 `RAG: <query>` 检索
- 未配置 Embedding 时使用词法 Jaccard；配置后优先语义向量检索
- Postgres 若安装 pgvector 扩展，检索走向量距离排序；否则在应用内算余弦

## 环境变量

| 变量 | 说明 |
|------|------|
| `EMBEDDING_BASE_URL` | OpenAI 兼容基址，如 `https://api.openai.com/v1` |
| `EMBEDDING_API_KEY` | API Key |
| `EMBEDDING_MODEL` | 默认 `text-embedding-3-small` |
| `RAG_MAX_UPLOAD_MB` | 上传上限，默认 32 |
| `RAG_CANDIDATE_LIMIT` | 词法候选上限，默认 2000 |

## 部署提示

- DB 镜像建议 `pgvector/pgvector:pg16`（或自建带 vector 扩展的 Postgres）
- API 启动时会尝试 `CREATE EXTENSION IF NOT EXISTS vector`
- Agent：编辑页绑定「知识库」后需含 `rag_query` 权限（绑定语料时会自动追加）
