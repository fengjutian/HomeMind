"""One-shot adapter: align upstream's self-update tests with the HomeMind package identity."""

from __future__ import annotations

import io
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "tests" / "unit" / "infra" / "setup" / "test_self_update.py"

REPLACEMENTS: list[tuple[str, str]] = [
    # distribution / module identity of the installed package under test
    ("Name: octop", "Name: homemind"),
    ('f"octop-{version}.dist-info"', 'f"homemind-{version}.dist-info"'),
    ('"octop-1.0.2b4.dist-info"', '"homemind-1.0.2b4.dist-info"'),
    ('"octop-1.0.2b5.dist-info"', '"homemind-1.0.2b5.dist-info"'),
    ('"octop-1.0.2b5.dist-info/METADATA"', '"homemind-1.0.2b5.dist-info/METADATA"'),
    ('"octop-1.0.2b5-py3-none-any.whl"', '"homemind-1.0.2b5-py3-none-any.whl"'),
    ('metadata("octop", "1.0.2b4"', 'metadata("homemind", "1.0.2b4"'),
    ('"octop==1.0.2b5"', '"homemind==1.0.2b5"'),
    ("'octop v' + __version__", "'homemind v' + __version__"),
    ('target / "octop" / "__init__.py"', 'target / "homemind" / "__init__.py"'),
    ('(tmp_path / "octop" / "__init__.py")', '(tmp_path / "homemind" / "__init__.py")'),
    ('(tmp_path / "octop" / "__main__.py")', '(tmp_path / "homemind" / "__main__.py")'),
    # helper names follow the package they write
    ("_write_octop_package", "_write_homemind_package"),
    ("_write_octop_metadata", "_write_homemind_metadata"),
    # the running application under test is HomeMind, not the upstream package
    ("    import octop\n    from octop.infra.setup import self_update", "    import homemind\n    from octop.infra.setup import self_update"),
    ('monkeypatch.setattr(octop, "__version__", "1.0.2b4")', 'monkeypatch.setattr(homemind, "__version__", "1.0.2b4")'),
]


def main() -> None:
    text = io.open(TARGET, encoding="utf-8", newline="").read()
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    io.open(TARGET, "w", encoding="utf-8", newline="").write(text)
    print("adapted", TARGET)


if __name__ == "__main__":
    main()
