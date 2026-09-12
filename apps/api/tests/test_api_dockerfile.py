from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_api_image_includes_node_for_stdio_mcp_npx():
    dockerfile = (ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")

    assert "nodejs npm" in dockerfile
    assert "npx -y @getnote/mcp" in dockerfile
