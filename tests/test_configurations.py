from pathlib import Path

import pytest
from pytest import MonkeyPatch

from ymmsl2svg import ymmsl2svg
from ymmsl2svg.settings import settings

test_ymmsl_folder = Path(__file__).parent / "configurations"
configuration_files = list(test_ymmsl_folder.glob("*.ymmsl"))
svg_output_folder = Path(__file__).parent.parent / "test_output"


@pytest.fixture(scope="session", autouse=True)
def clear_output_folder():
    for svgfile in svg_output_folder.glob("*.svg"):
        svgfile.unlink()
    yield
    # Write HTML file embedding all images
    (svg_output_folder / "index.html").write_text(gen_index())


def gen_index() -> str:
    html = [
        "<html><head><style>"
        ".flex-grid{"
        "  display:flex;"
        "  flex-wrap:wrap;"
        "  gap:12px;"
        "}"
        ".flex-grid div{"
        "  border:1px solid black;"
        "  padding:5px;"
        "  justify-items: center;"
        "}"
        "#debug:not(:checked) ~ div .debug {display:none;}"
        "#debug:checked ~ div .nodebug {display:none;}"
        "</style></head>"
        "<body style='background-color: #eee'>"
        "<input type='checkbox' id='debug'/><label for='debug'>Show debug</label>"
        "<h1>Generated SVG for tests</h1>"
        "<div class='flex-grid'>"
    ]
    for srcpath in configuration_files:
        output = outfile(srcpath, False).name
        html.append(f"<div><h2>{output}</h2>")
        html.append(f"<a class='nodebug' href='{output}'><image src='{output}'/></a>")
        output = outfile(srcpath, True).name
        html.append(f"<a class='debug' href='{output}'><image src='{output}'/></a>")
        html.append("</div>")
    html.append("</div></body></html>")
    return "".join(html)


def _fname(path: Path) -> str:
    return path.name


def outfile(srcpath: Path, debug: bool) -> Path:
    suffix = ".debug.svg" if debug else ".svg"
    return svg_output_folder / srcpath.with_suffix(suffix).name


@pytest.mark.parametrize("path", configuration_files, ids=_fname)
@pytest.mark.parametrize("debug", [False, True], ids=["nodebug", "debug"])
def test_ymmsl2svg(monkeypatch: MonkeyPatch, path: Path, debug: bool) -> None:
    monkeypatch.setattr(settings, "debug", debug)
    svg = ymmsl2svg(path)
    outfile(path, debug).write_text(str(svg))
