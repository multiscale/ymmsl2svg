from pathlib import Path

import pytest

from ymmsl2svg import ymmsl2svg

test_ymmsl_folder = Path(__file__).parent / "configurations"
configuration_files = list(test_ymmsl_folder.glob("*.ymmsl"))
svg_output_folder = Path(__file__).parent.parent / "test_output"


@pytest.fixture(scope="session", autouse=True)
def clear_output_folder():
    for svgfile in svg_output_folder.glob("*.svg"):
        svgfile.unlink()


def _fname(path: Path) -> str:
    return path.name


@pytest.mark.parametrize("path", configuration_files, ids=_fname)
def test_ymmsl2svg(path: Path) -> None:
    svg = ymmsl2svg(path)

    outfile = svg_output_folder / path.with_suffix(".svg").name
    outfile.write_text(str(svg))
