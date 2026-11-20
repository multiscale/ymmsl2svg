import itertools
from pathlib import Path

import pytest
import ymmsl
from ymmsl.v0_2 import Configuration, Model

from ymmsl2svg.model_block import ModelBlock


def ordered_names(model: Model) -> list[str]:
    """Names of the components in the model timeline, in drawing order."""
    model_block = ModelBlock(model)
    return [str(c.component.name) for c in model_block.timeline_block.components]


def test_dispatch3_order():
    fname = Path(__file__).parent / "configurations" / "dispatch3.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        assert ordered_names(model) == ["first", "second", "third"]


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="Matching timelines not supported"
)
def test_timeline_bridge_order():
    fname = Path(__file__).parent / "configurations" / "timescale-bridge.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        idx = {comp.name: i for i, comp in enumerate(perm)}
        expected = ["A", "bridge", "B"] if idx["A"] < idx["B"] else ["B", "bridge", "A"]
        assert ordered_names(model) == expected
