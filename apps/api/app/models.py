import json
from datetime import datetime
from sqlalchemy import String, Text, Boolean, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def _json_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[str] = mapped_column(Text, default='["user"]')
    assignable_roles: Mapped[str] = mapped_column(Text, default="[]")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self, include_tokens: bool = False) -> dict:
        tokens = _json_list(self.tokens)
        d = {
            "username": self.username,
            "role": "",
            "roles": _json_list(self.roles),
            "assignable_roles": _json_list(self.assignable_roles),
            "disabled": self.disabled,
            "permission": _json_list(self.permissions),
            "token": tokens if include_tokens else [],
            "token_count": len(tokens),
        }
        return d


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMResource(Base):
    __tablename__ = "llm_resources"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="llm")  # llm | group
    name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64), default="openai")
    base_url: Mapped[str] = mapped_column(String(512), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    members: Mapped[str] = mapped_column(Text, default="[]")
    description: Mapped[str] = mapped_column(Text, default="")
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=128000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self, mask_key: bool = True) -> dict:
        from app.security import decrypt_secret, mask_secret
        key = decrypt_secret(self.api_key_enc) if self.api_key_enc else ""
        d = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": mask_secret(key) if mask_key else key,
            "model": self.model,
            "members": _json_list(self.members),
            "description": self.description,
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }
        return d


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    zip_path: Mapped[str] = mapped_column(String(512), default="")
    zip_name: Mapped[str] = mapped_column(String(255), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tags": self.tags,
            "description": self.description,
            "has_zip": bool(self.zip_path),
            "zip_name": self.zip_name,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class MCP(Base):
    __tablename__ = "mcps"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    headers: Mapped[str] = mapped_column(Text, default="{}")
    protocol: Mapped[str] = mapped_column(String(32), default="sse")
    command: Mapped[str] = mapped_column(String(255), default="")
    command_args: Mapped[str] = mapped_column(Text, default="[]")
    command_env: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        env = {}
        try:
            env = json.loads(self.command_env or "{}")
            if not isinstance(env, dict):
                env = {}
        except Exception:
            env = {}
        return {
            "id": self.id,
            "name": self.name,
            "tags": self.tags,
            "url": self.url,
            "headers": self.headers,
            "protocol": self.protocol,
            "command": self.command or "",
            "command_args": _json_list(self.command_args),
            "command_env": env,
            "description": self.description,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class Sandbox(Base):
    __tablename__ = "sandboxes"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    image: Mapped[str] = mapped_column(String(255), default="python:3.12-slim")
    cpu_count: Mapped[int] = mapped_column(Integer, default=2)
    memory_mb: Mapped[int] = mapped_column(Integer, default=512)
    network_mode: Mapped[str] = mapped_column(String(32), default="bridge")
    port_mappings: Mapped[str] = mapped_column(String(512), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    container_id: Mapped[str] = mapped_column(String(128), default="")
    container_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="stopped")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        from app.config import get_settings
        from pathlib import Path
        settings = get_settings()
        wp = Path(settings.workplace_dir).resolve() / self.id / "workplace"
        wp.mkdir(parents=True, exist_ok=True)
        skills_path = str(Path(settings.data_dir).resolve() / "skills")
        engine_path = str(Path(settings.data_dir).resolve() / "engine")
        mcp_path = str(Path(settings.data_dir).resolve() / "mcp")
        llm_path = str(Path(settings.data_dir).resolve() / "llm")
        mounts = {
            skills_path: {"bind": "/skills", "mode": "ro"},
            engine_path: {"bind": "/engine", "mode": "ro"},
            mcp_path: {"bind": "/mcp", "mode": "ro"},
            llm_path: {"bind": "/llm", "mode": "ro"},
            str(wp): {"bind": "/workplace", "mode": "rw"},
        }
        return {
            "id": self.id,
            "name": self.name,
            "image": self.image,
            "cpu_count": self.cpu_count,
            "memory_mb": self.memory_mb,
            "network_mode": self.network_mode,
            "port_mappings": self.port_mappings,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "skills_path": skills_path,
            "engine_path": engine_path,
            "mcp_path": mcp_path,
            "llm_path": llm_path,
            "workplace_path": str(wp),
            "workplace_mount": "/workplace",
            "mounts": mounts,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "status": self.status,
            "creator": self.creator,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    engine: Mapped[str] = mapped_column(String(32), default="react")
    llm_id: Mapped[str] = mapped_column(String(16), default="")
    sandbox_id: Mapped[str] = mapped_column(String(16), default="")
    skills: Mapped[str] = mapped_column(Text, default="[]")
    mcps: Mapped[str] = mapped_column(Text, default="[]")
    rags: Mapped[str] = mapped_column(Text, default="[]")
    max_iterations: Mapped[int] = mapped_column(Integer, default=150)
    history_length: Mapped[int] = mapped_column(Integer, default=30)
    summary_max_words: Mapped[int] = mapped_column(Integer, default=5000)
    proactivity: Mapped[int] = mapped_column(Integer, default=2)
    llm_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    skill_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    shell_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    mcp_soft_circuit: Mapped[int] = mapped_column(Integer, default=5)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    allowed_actions: Mapped[str] = mapped_column(Text, default='["self_ask","skill_read_md","skill_read_script","skill_run_script","mcp_tool_call","httpmcp_call","shell","file_read","file_write","file_search","file_search_replace"]')
    memory: Mapped[str] = mapped_column(Text, default="")
    session_list: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "engine": self.engine,
            "llm": self.llm_id,
            "sandbox": self.sandbox_id,
            "skills": _json_list(self.skills),
            "mcps": _json_list(self.mcps),
            "rags": _json_list(self.rags),
            "max_iterations": self.max_iterations,
            "history_length": self.history_length,
            "summary_max_words": self.summary_max_words,
            "proactivity": self.proactivity,
            "llm_timeout": self.llm_timeout,
            "skill_timeout": self.skill_timeout,
            "shell_timeout": self.shell_timeout,
            "mcp_soft_circuit": self.mcp_soft_circuit,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "allowed_actions": _json_list(self.allowed_actions),
            "creator": self.creator,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "session_list": _json_list(self.session_list),
        }


class AgentTick(Base):
    __tablename__ = "agent_ticks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tick_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    cron: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    creator: Mapped[str] = mapped_column(String(64), default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ChatNote(Base):
    __tablename__ = "chat_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text, default="")


class ChatSummary(Base):
    __tablename__ = "chat_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text, default="")


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    announcement: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    members: Mapped[str] = mapped_column(Text, default="[]")
    session_list: Mapped[str] = mapped_column(Text, default="[]")
    history_length: Mapped[int] = mapped_column(Integer, default=10)
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "announcement": self.announcement,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "members": _json_list(self.members),
            "member_count": len(_json_list(self.members)),
            "session_list": _json_list(self.session_list),
            "history_length": self.history_length,
            "creator": self.creator,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }


class GroupWorkflow(Base):
    __tablename__ = "group_workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    task: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="idle")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class SqlServer(Base):
    __tablename__ = "sql_servers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=3306)
    db_type: Mapped[str] = mapped_column(String(32), default="mysql")
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    database: Mapped[str] = mapped_column(String(128), default="")
    query_limit: Mapped[int] = mapped_column(Integer, default=100)
    connect_timeout: Mapped[int] = mapped_column(Integer, default=10)
    read_timeout: Mapped[int] = mapped_column(Integer, default=60)
    write_timeout: Mapped[int] = mapped_column(Integer, default=60)
    remark: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[str] = mapped_column(String(64), default="")

    def to_dict(self, mask_pw: bool = True) -> dict:
        from app.security import decrypt_secret, mask_secret
        pw = decrypt_secret(self.password_enc) if self.password_enc else ""
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "db_type": self.db_type,
            "username": self.username,
            "password": mask_secret(pw) if mask_pw else pw,
            "database": self.database,
            "query_limit": self.query_limit,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "write_timeout": self.write_timeout,
            "remark": self.remark,
            "creator": self.creator,
        }


class SqlHistory(Base):
    __tablename__ = "sql_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    server_id: Mapped[str] = mapped_column(String(16), default="")
    sql_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class SshServer(Base):
    __tablename__ = "ssh_servers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128), default="root")
    auth_type: Mapped[str] = mapped_column(String(16), default="password")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    private_key_enc: Mapped[str] = mapped_column(Text, default="")
    passphrase_enc: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "description": self.description,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
        }


class FileShare(Base):
    __tablename__ = "file_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str] = mapped_column(String(255))
    share_token: Mapped[str] = mapped_column(String(64), unique=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class RagCorpus(Base):
    __tablename__ = "rag_corpus"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(512), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    index_error: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "file_path": self.file_path,
            "chunk_count": self.chunk_count,
            "index_status": self.index_status or "pending",
            "index_error": self.index_error or "",
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class HttpMcp(Base):
    __tablename__ = "http_mcps"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    url: Mapped[str] = mapped_column(String(512), default="")
    method: Mapped[str] = mapped_column(String(16), default="POST")
    headers: Mapped[str] = mapped_column(Text, default="{}")
    body_template: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    auth_json: Mapped[str] = mapped_column(Text, default="[]")
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def _tools(self) -> list:
        tools = _json_list(self.tools_json)
        if tools:
            return tools
        # Legacy single-endpoint → one synthetic tool
        if self.url:
            try:
                hdr = json.loads(self.headers or "{}")
            except Exception:
                hdr = {}
            headers = [{"key": k, "value": str(v)} for k, v in (hdr or {}).items()]
            return [
                {
                    "id": "default",
                    "name": self.name or "default",
                    "method": self.method or "POST",
                    "url": self.url,
                    "description": self.description or "",
                    "timeout": 300,
                    "headers": headers,
                    "fixed_args": [],
                    "args": [],
                    "body_template": self.body_template or "",
                }
            ]
        return []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version or "1.0.0",
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
            "body_template": self.body_template,
            "description": self.description,
            "auth": _json_list(self.auth_json),
            "tools": self._tools(),
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class SiteConfig(Base):
    __tablename__ = "site_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")


class RoleGrant(Base):
    __tablename__ = "role_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    roles: Mapped[str] = mapped_column(Text, default="[]")


class RoleDefinition(Base):
    __tablename__ = "role_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "permissions": _json_list(self.permissions),
            "builtin": self.builtin,
        }


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    corpus_id: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[str] = mapped_column(Text, default="[]")
    embedding_vec: Mapped[str] = mapped_column(Text, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)


class GroupChatMessage(Base):
    __tablename__ = "group_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ImChannel(Base):
    """External IM bot channel (Feishu / DingTalk / Telegram / QQ / WeCom / mock)."""

    __tablename__ = "im_channels"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), index=True)  # mock|feishu|dingtalk|telegram|qq|wecom
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    # Non-secret + secret fields as JSON; secrets stored encrypted via encrypt_secret on whole blob
    config_enc: Mapped[str] = mapped_column(Text, default="")
    webhook_secret: Mapped[str] = mapped_column(String(64), default="")  # path token for URL
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_event_at: Mapped[str] = mapped_column(String(32), default="")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")

    def get_config(self) -> dict:
        from app.security import decrypt_secret
        raw = decrypt_secret(self.config_enc) if self.config_enc else "{}"
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_config(self, cfg: dict) -> None:
        from app.security import encrypt_secret
        self.config_enc = encrypt_secret(json.dumps(cfg or {}, ensure_ascii=False))

    def to_dict(self, mask_secrets: bool = True) -> dict:
        from app.security import mask_secret
        cfg = self.get_config()
        if mask_secrets:
            secret_keys = {
                "app_secret", "secret", "bot_token", "access_token", "encrypt_key",
                "encoding_aes_key", "client_secret", "token", "verification_token",
            }
            cfg = {
                k: (mask_secret(str(v)) if k in secret_keys and v else v)
                for k, v in cfg.items()
            }
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "enabled": self.enabled,
            "agent_id": self.agent_id,
            "config": cfg,
            "webhook_secret": self.webhook_secret,
            "webhook_path": f"/hooks/channels/{self.provider}/{self.id}/{self.webhook_secret}",
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
            "creator": self.creator,
            "modified_at": self.modified_at,
            "visibility": self.visibility or "private",
            "allowed_users": _json_list(self.allowed_users),
        }


class ImSession(Base):
    __tablename__ = "im_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    external_chat_id: Mapped[str] = mapped_column(String(128), index=True)
    external_user_id: Mapped[str] = mapped_column(String(128), default="")
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_session_id: Mapped[str] = mapped_column(String(32), index=True)
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class ImDedup(Base):
    __tablename__ = "im_dedup"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    msg_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ImEventLog(Base):
    __tablename__ = "im_event_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
