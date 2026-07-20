import itertools
from pathlib import Path

import ymmsl
from ymmsl.v0_2 import Configuration

from ymmsl2svg.timeline_node import create_timeline_tree


def test_dispatch3_order():
    fname = Path(__file__).parent / "configurations" / "dispatch3.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        rootnode = create_timeline_tree(model)
        expected = ["first", "second", "third"]
        assert [comp.name for comp in rootnode.components] == expected


def test_timeline_bridge_order():
    fname = Path(__file__).parent / "configurations" / "timescale-bridge.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        rootnode = create_timeline_tree(model)

        idx = {comp.name: i for i, comp in enumerate(perm)}
        expected = ["A", "bridge", "B"] if idx["A"] < idx["B"] else ["B", "bridge", "A"]
        assert [comp.name for comp in rootnode.components] == expected
