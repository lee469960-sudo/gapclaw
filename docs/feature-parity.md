# GAP — Feature Parity Matrix (v1.9.0)

Target: GAP v1.9.0 | Clone: 1.9.0-clone | Updated: 2026-07-09

| Module | Route | API | UI | Runtime |
|--------|-------|-----|-----|---------|
| 登录 | `/login` | Ready | Ready | Ready |
| 智能体群 | `/groups` | Ready | Ready | Ready |
| 群聊 | `/groups/:id/chat` | Ready | Ready | Ready |
| 智能体 | `/agents` | Ready | Ready | Partial |
| Agent 对话 | `/agents/:id/chat` | Ready | Ready | Ready |
| 沙箱 | `/sandboxes` | Ready | Ready | Ready |
| Skills | `/skills` | Ready | Ready | Ready |
| MCP | `/mcps` | Ready | Ready | Partial |
| LLM | `/llms` | Ready | Ready | Ready |
| 文件 | `/files` | Ready | Partial | Ready |
| RAG | `/rag` | Ready | Ready | Ready* |

\*RAG Runtime：需在 Agent 中绑定知识库；语义检索需配置 `EMBEDDING_*`，否则词法回退。详见 [rag.md](rag.md)。
| HttpMCP | `/httpmcp` | Ready | Partial | Ready |
| 消息渠道 | `/channels` | Ready | Ready | Ready |
| SQL | `/sql` | Ready | Partial | Ready |
| SSH | `/terminals` | Ready | Ready | Ready |
| Docker | `/docker` | Ready | Partial | Ready |
| 监控 | `/monitor` | Ready | Partial | Ready |
| 站点 | `/site` | Ready | Ready | Ready |
| 个人中心 | `/me` | Ready | Ready | Ready |
| 用户管理 | `/users` | Ready | Ready | Ready |
| 角色管理 | `/roles` | Ready | Ready | Ready |
| 对外 API | — | Ready | — | Ready |

**Legend:** Ready = production-usable | Partial = core works, polish pending

## Wave Status

- W0 基线对照: Done
- W1 认证/RBAC: Done
- W2 LLM/Skills/MCP/HttpMCP: Done
- W3 沙箱PTY/文件: Done
- W4 Agent/ReAct/Tick: Done
- W5 群聊/工作流: Done
- W6 SQL/SSH/Docker/Monitor: Done
- W7 RAG: Done
- W8 E2E smoke: Done
