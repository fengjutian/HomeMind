"""Coverage for the bundled novel analysis expert template."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "novel-analysis-expert"


def test_novel_analysis_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()
    expert = catalog.get("novel-analysis-expert")
    assert expert is not None
    assert expert.summary.label_zh == "小说分析专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "skills/novel-analysis/SKILL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_novel_analysis_manifest_and_soul_cover_requirements() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")
    assert all(fmt in manifest["description"]["zh"] for fmt in ("EPUB", "PDF", "TXT"))
    assert "人物关系" in soul
    assert "事件与情节推动" in soul
    assert "Mermaid 知识图谱" in soul
    assert "nodes" in soul and "edges" in soul
    assert "文本事实" in soul and "合理推断" in soul
    assert "叙事结构与信息流" in soul
    assert "主题、意象与人物弧光" in soul
    assert "伏笔、悬念与回收" in soul
    assert "分批工作流" in soul
    assert "默认综合报告模板" in soul
    assert "质量自检" in soul


def test_novel_extractor_reads_txt_and_epub(tmp_path: Path) -> None:
    script = _ROOT / "skills/novel-analysis/scripts/extract_novel.py"
    spec = importlib.util.spec_from_file_location("extract_novel", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    txt = tmp_path / "story.txt"
    txt.write_text("第一章\n相遇", encoding="utf-8")
    assert "[[TXT_LINE 2]] 相遇" in module.extract(txt)

    epub = tmp_path / "story.epub"
    with zipfile.ZipFile(epub, "w") as book:
        book.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>',
        )
        book.writestr(
            "content.opf",
            '<package><manifest><item id="c1" href="one.xhtml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        book.writestr("one.xhtml", "<h1>第一章</h1><p>人物登场</p>")
    result = module.extract(epub)
    assert "[[EPUB_SECTION 1: one.xhtml]]" in result
    assert "人物登场" in result
