"""Local Codex plugin packaging and bounded personal MCP behavior (synthetic)."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "plugins" / "knowledge-forge"
SERVER = ROOT / "server" / "personal_mcp.py"


def module():
    spec = importlib.util.spec_from_file_location("kf_codex_personal_mcp_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class CodexDesktopPluginTests(unittest.TestCase):
    def test_manifest_marketplace_mcp_and_skill_paths(self):
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads(
            (REPO / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "toadaid-knowledge-forge")
        self.assertTrue(manifest["extensions"]["com.openai"]["requires_local_executor"])
        self.assertTrue((ROOT / manifest["extensions"]["com.openai"]["onboardingSkill"][2:]).is_file())
        self.assertEqual(
            marketplace["plugins"][0]["source"]["path"],
            "./plugins/knowledge-forge",
        )
        self.assertEqual(mcp["mcpServers"]["knowledge_forge_personal"]["type"], "stdio")
        self.assertEqual(mcp["mcpServers"]["knowledge_forge_personal"]["command"], "python3")
        self.assertIn("personal_mcp.py", " ".join(mcp["mcpServers"]["knowledge_forge_personal"]["args"]))
        self.assertTrue((ROOT / "skills/personal-knowledge/SKILL.md").is_file())

    def test_tool_roster_is_read_only(self):
        mod = module()
        self.assertEqual(set(mod.TOOLS), {"library_list", "knowledge_lookup"})
        self.assertTrue(all(x["annotations"]["readOnlyHint"] for x in mod.TOOLS.values()))
        self.assertFalse(any("promot" in name or "approve" in name for name in mod.TOOLS))

    def test_tool_validation_refuses_widening(self):
        mod = module()
        for name,args in [
            ("promote", {}),
            ("library_list", {"unwanted": True}),
            ("knowledge_lookup", {"question": "yes", "max_units": 99}),
            ("knowledge_lookup", {"question": "yes", "max_units": True}),
            ("knowledge_lookup", {"question": " ", "max_units": 1}),
            ("knowledge_lookup", {"question": "yes", "library": "/tmp/x"}),
        ]:
            self.assertTrue(mod.call(name, args)["isError"])

    def test_bounded_lookup_args_and_exact_cli_result(self):
        mod = module()
        payload = {
            "personal_library_mode": True,
            "trust_class": "UNTRUSTED_KNOWLEDGE_DATA",
            "reasoning_context": [{"source_bindings": [{"source_id": "s1"}]}],
            "tool_grants": [],
        }
        completed = mock.Mock(returncode=0,
                              stdout=json.dumps(payload).encode(), stderr=b"")
        with (mock.patch.object(mod.shutil, "which", return_value="/bin/kf"),
              mock.patch.object(mod.subprocess, "run", return_value=completed) as run):
            result = mod.call("knowledge_lookup", {"question": "patience", "max_units": 2})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        self.assertEqual(
            run.call_args.args[0],
            ["/bin/kf", "personal", "lookup", "patience", "--max-units", "2"],
        )

    def test_missing_cli_returns_clear_refusal(self):
        mod = module()
        with mock.patch.object(mod.shutil, "which", return_value=None):
            result = mod.call("library_list", {})
        self.assertTrue(result["isError"])
        self.assertIn("not installed", result["structuredContent"]["error"])

    def test_jsonrpc_init_tools_and_unavailable_cli(self):
        mod = module()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "library_list", "arguments": {}}},
        ]
        inp = io.StringIO("\n".join(json.dumps(x) for x in requests)+"\n")
        out = io.StringIO()
        with mock.patch.object(mod.shutil, "which", return_value=None):
            mod.serve(inp, out)
        rows = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([x["id"] for x in rows], [1, 2, 3])
        self.assertEqual(len(rows[1]["result"]["tools"]), 2)
        self.assertTrue(rows[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
