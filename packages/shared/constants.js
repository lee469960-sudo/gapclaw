export const API_CODE_OK = 0

export const ALLOWED_ACTIONS = [
  'self_ask',
  'skill_read_md',
  'skill_read_script',
  'skill_run_script',
  'mcp_tool_call',
  'httpmcp_call',
  'shell',
  'file_read',
  'file_write',
  'file_search',
  'file_search_replace',
  'rag_query',
]

export const PAGE_ROUTES = {
  '/pages/page_group.cgi': '/groups',
  '/pages/page_agent.cgi': '/agents',
  '/pages/page_sandbox.cgi': '/sandboxes',
  '/pages/page_skills.cgi': '/skills',
  '/pages/page_mcp.cgi': '/mcps',
  '/pages/page_llm.cgi': '/llms',
  '/pages/page_files.cgi': '/files',
  '/pages/page_rag.cgi': '/rag',
  '/pages/page_httpmcp.cgi': '/httpmcp',
  '/pages/page_sql.cgi': '/sql',
  '/pages/page_terminal.cgi': '/terminals',
  '/pages/page_docker.cgi': '/docker',
  '/pages/page_monitor.cgi': '/monitor',
  '/pages/page_site.cgi': '/site',
  '/pages/system_me.cgi': '/me',
  '/pages/system_user.cgi': '/users',
  '/pages/system_role.cgi': '/roles',
}
