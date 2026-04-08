"""
PatchTool 单元测试

测试 patch 生成的各种场景：
- 基本增/删/改
- 空内容处理
- 相同内容处理
- 大文件 diff
- 安全路径拦截
"""

import asyncio
import sys
import tempfile
from pathlib import Path

# 确保 src 在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cc_agent.tools.patch import PatchTool


async def run_all_tests():
    """运行所有测试用例"""
    tool = PatchTool(project_root=str(Path(__file__).parent.parent))

    tests = [
        test_basic_add,
        test_basic_remove,
        test_basic_replace,
        test_multiline_change,
        test_same_content,
        test_empty_old,
        test_empty_new,
        test_with_description,
        test_sensitive_path,
        test_large_diff_truncation,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        name = test_fn.__name__
        try:
            await test_fn(tool)
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print(f"{'=' * 60}")
    return failed == 0


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

async def test_basic_add(tool: PatchTool):
    """测试：纯新增内容"""
    result = await tool.call({
        "path": "test.py",
        "old_content": "line1\nline2\n",
        "new_content": "line1\nline2\nline3\n",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "---" in result["diff"], "diff should contain --- header"
    assert "+++" in result["diff"], "diff should contain +++ header"
    assert "+line3" in result["diff"], "diff should show added line"
    assert "+1 行" in result["preview"], f"preview should show +1: {result['preview']}"
    assert "-0 行" in result["preview"], f"preview should show -0: {result['preview']}"


async def test_basic_remove(tool: PatchTool):
    """测试：纯删除内容"""
    result = await tool.call({
        "path": "test.py",
        "old_content": "line1\nline2\nline3\n",
        "new_content": "line1\nline3\n",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "-line2" in result["diff"], "diff should show removed line"
    assert "-1 行" in result["preview"], f"preview should show -1: {result['preview']}"
    assert "+0 行" in result["preview"], f"preview should show +0: {result['preview']}"


async def test_basic_replace(tool: PatchTool):
    """测试：替换内容"""
    result = await tool.call({
        "path": "test.py",
        "old_content": "def hello():\n    print('world')\n",
        "new_content": "def hello():\n    print('hello')\n",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "-    print('world')" in result["diff"], "should show old line"
    assert "+    print('hello')" in result["diff"], "should show new line"


async def test_multiline_change(tool: PatchTool):
    """测试：多行同时变更"""
    old = "import os\nimport sys\n\ndef main():\n    pass\n"
    new = "import os\nimport sys\nimport json\n\ndef main():\n    print('hello')\n"
    result = await tool.call({
        "path": "app.py",
        "old_content": old,
        "new_content": new,
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "+import json" in result["diff"], "should show added import"
    assert "-    pass" in result["diff"], "should show removed pass"
    assert "+    print('hello')" in result["diff"], "should show new print"


async def test_same_content(tool: PatchTool):
    """测试：内容完全相同应报错"""
    content = "line1\nline2\n"
    result = await tool.call({
        "path": "test.py",
        "old_content": content,
        "new_content": content,
    })
    assert result.get("success") is False, "should fail for same content"
    assert "相同" in result.get("error", ""), f"error should mention same content: {result}"


async def test_empty_old(tool: PatchTool):
    """测试：从空内容创建新文件"""
    result = await tool.call({
        "path": "new_file.py",
        "old_content": "",
        "new_content": "# new file\nprint('hello')\n",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "+# new file" in result["diff"], "should show added lines"
    assert "diff" in result, "should have diff field"


async def test_empty_new(tool: PatchTool):
    """测试：清空文件内容"""
    result = await tool.call({
        "path": "to_delete.py",
        "old_content": "# delete me\nprint('gone')\n",
        "new_content": "",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "-# delete me" in result["diff"], "should show removed lines"


async def test_with_description(tool: PatchTool):
    """测试：带修改说明"""
    result = await tool.call({
        "path": "app.py",
        "old_content": "x = 1\n",
        "new_content": "x = 2\n",
        "description": "修改默认值",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "修改默认值" in result.get("message", ""), f"message should contain description: {result}"
    assert "修改默认值" in result.get("preview", ""), f"preview should contain description: {result}"


async def test_sensitive_path(tool: PatchTool):
    """测试：敏感路径应被拦截"""
    result = await tool.call({
        "path": "/etc/passwd",
        "old_content": "old",
        "new_content": "new",
    })
    assert result.get("success") is False, "should block sensitive path"
    assert "安全限制" in result.get("error", ""), f"should mention security: {result}"


async def test_large_diff_truncation(tool: PatchTool):
    """测试：大量变更时 preview 应截断"""
    old_lines = [f"old line {i}" for i in range(100)]
    new_lines = [f"new line {i}" for i in range(100)]
    result = await tool.call({
        "path": "big_file.py",
        "old_content": "\n".join(old_lines) + "\n",
        "new_content": "\n".join(new_lines) + "\n",
    })
    assert result.get("success") is True, f"should succeed: {result}"
    assert "diff" in result, "should have diff"
    assert "preview" in result, "should have preview"
    # preview 应该被截断或包含截断提示
    preview_lines = result["preview"].split("\n")
    assert len(preview_lines) < 200, f"preview should be bounded, got {len(preview_lines)} lines"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("PatchTool 单元测试")
    print("=" * 60)
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
