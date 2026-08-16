#!/usr/bin/env python3
"""Extended API smoke test for GAP v1.9.0."""
import httpx

BASE = "http://localhost:8000"
client = httpx.Client(base_url=BASE, timeout=30)

r = client.get("/captcha.cgi")
captcha_data = r.json()
assert captcha_data["code"] == 0, r.text
captcha_code = captcha_data["data"]["code"]
captcha_cookies = r.cookies

r = client.post(
    "/login.cgi",
    json={"username": "admin", "password": "admin123", "captcha": captcha_code},
    cookies=captcha_cookies,
)
assert r.json()["code"] == 0, r.text
cookies = r.cookies
print("OK login")

endpoints = [
    "/health",
    "/render.cgi",
    "/pages/system_me.cgi",
    "/pages/system_user.cgi?action=list",
    "/pages/system_role.cgi",
    "/pages/page_llm.cgi?action=list",
    "/pages/page_agent.cgi?action=list",
    "/pages/page_sandbox.cgi?action=list",
    "/pages/page_skills.cgi?action=list",
    "/pages/page_mcp.cgi?action=list",
    "/pages/page_httpmcp.cgi?action=list",
    "/pages/page_channel.cgi",
    "/pages/page_group.cgi?action=list",
    "/pages/page_sql.cgi?action=servers",
    "/pages/page_terminal.cgi?action=list",
    "/pages/page_files.cgi?action=list&path=",
    "/pages/page_rag.cgi?action=list",
    "/pages/page_monitor.cgi",
    "/pages/page_docker.cgi",
    "/pages/page_site.cgi",
]

for ep in endpoints:
    r = client.get(ep, cookies=cookies)
    if ep == "/health":
        assert r.status_code == 200
    else:
        data = r.json()
        assert data.get("code") == 0, f"{ep} failed: {data}"
    print(f"OK {ep}")

render = client.get("/render.cgi", cookies=cookies).json()
assert render["data"]["version"].startswith("1.9"), "version should be 1.9.x"
print(f"OK version={render['data']['version']}")

print("All smoke tests passed.")
