import itertools
from pathlib import Path

import ymmsl
from ymmsl.v0_2 import Configuration

from ymmsl2svg.timeline_node import create_timeline_nodes


def test_dispatch3_order():
    fname = Path(__file__).parent / "configurations" / "dispatch3.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        rootnode = create_timeline_nodes(model)
        expected = ["first", "second", "third"]
        assert [comp.name for comp in rootnode.components] == expected


def test_timeline_bridge_order():
    fname = Path(__file__).parent / "configurations" / "timescale-bridge.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all six permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        rootnode = create_timeline_nodes(model)

        idx = {comp.name: i for i, comp in enumerate(perm)}
        expected = ["A", "bridge", "B"] if idx["A"] < idx["B"] else ["B", "bridge", "A"]
        assert [comp.name for comp in rootnode.components] == expected


def test_interact_order():
    fname = Path(__file__).parent / "configurations" / "interact-dispatch.ymmsl"
    configuration = ymmsl.load_as(Configuration, fname)
    model = configuration.root_model()

    # Generate all permutations of components, and check we order them correctly
    for perm in itertools.permutations(list(model.components.values())):
        model.components = {component.name: component for component in perm}
        rootnode = create_timeline_nodes(model)

        # The interacting components stay side by side, in definition order, between
        # the component they get their F_INIT messages from and the one they send
        # their O_F messages to
        idx = {comp.name: i for i, comp in enumerate(perm)}
        interact = sorted(["left", "right"], key=lambda name: idx[name])
        expected = ["init", *interact, "end"]
        assert [comp.name for comp in rootnode.components] == expected
        # Each interacting component keeps its own (matching) subtimeline
        parents = {
            str(tl): [comp.name for comp in node.parent_components]
            for tl, node in rootnode.children.items()
        }
        assert parents == {"left": ["left"], "right": ["right"]}
