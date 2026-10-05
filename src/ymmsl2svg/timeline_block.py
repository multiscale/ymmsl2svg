from collections.abc import Callable, Iterator

import svg
from ymmsl.v0_2 import Reference

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.conduit_ducts import ConduitDuct, TopConduitDuct
from ymmsl2svg.settings import settings
from ymmsl2svg.timeline_node import TimelineNode


class TimelineBlock(SvgBlock):
    """Container for everything inside a timeline.

    This component is responsible for the layout and ordering of all components inside a
    yMMSL timeline. Subtimelines are instantiated and layed out recursively.
    """

    def __init__(self, node: TimelineNode) -> None:
        super().__init__()

        self.node = node
        if len(node.parent_components) > 1:
            raise NotImplementedError(
                "Visualization for interact coupling is not yet implemented."
            )

        self.transform: svg.Transform = svg.Translate(0, 0)
        self.min_top_height: float = 0
        """Minimum height of our top_conduit_duct, set by align_nesting_levels."""

        self.top_conduit_duct = TopConduitDuct(self, node.timeline)
        self.conduit_ducts: list[ConduitDuct] = [
            ConduitDuct(self.top_conduit_duct) for _ in range(len(node.components) + 1)
        ]
        self.components: list[ComponentBlock] = []

        # Subtimelines
        self.subtimelines: list[TimelineBlock] = []
        subtl_per_component: dict[Reference, list[TimelineBlock]] = {}
        for subnode in node.children.values():
            subtimeline = TimelineBlock(subnode)
            self.subtimelines.append(subtimeline)
            for component in subnode.parent_components:
                subtl_per_component.setdefault(component.name, []).append(subtimeline)

        # Order of components is determined by TimelineNode
        for i, component in enumerate(node.components):
            subtimelines = subtl_per_component.get(component.name, [])
            cblock = ComponentBlock(
                component,
                subtimelines,
                self.conduit_ducts[i],
                self.conduit_ducts[i + 1],
                self.node.timeline,
            )
            self.components.append(cblock)

    def _iter_cd_and_components(self) -> Iterator[ConduitDuct | ComponentBlock]:
        """Iterate over all ConduitDucts and ComponentBlocks (left to right)."""
        for i, component in enumerate(self.components):
            yield self.conduit_ducts[i]
            yield component
        yield self.conduit_ducts[-1]

    def map_components(self) -> dict[Reference, ComponentBlock]:
        """Recursively map all ComponentBlocks by their component name"""
        result = {c.component.name: c for c in self.components}
        for subtl in self.subtimelines:
            result.update(subtl.map_components())
        return result

    def get_component_sort_keys(self) -> dict[Reference, tuple[int, ...]]:
        """Recursively calculate the sort key of each component (for sorting ports)."""
        result = {}
        for i, component in enumerate(self.components):
            result[component.component.name] = (i,)
            for subtimeline in component.subtimelines:
                for compname, key in subtimeline.get_component_sort_keys().items():
                    result[compname] = (i,) + key
        return result

    def sort_output_ports(self, key: Callable) -> None:
        """Sort O_I/O_F ports in all components in this timeline and sub-timelines."""
        for component in self.components:
            component.sort_output_ports(key)
        for subtimeline in self.subtimelines:
            subtimeline.sort_output_ports(key)

    def sort_input_ports(self, key: Callable) -> None:
        """Sort F_INIT/S ports in all components in this timeline and sub-timelines."""
        for component in self.components:
            component.sort_input_ports(key)
        for subtimeline in self.subtimelines:
            subtimeline.sort_input_ports(key)

    def route_conduits(self) -> None:
        self.top_conduit_duct.route_conduits()

    def _boundary_duct_padding(self) -> tuple[float, float, float]:
        """Padding needed to keep a lone component's O_I/S port groups a consistent
        distance from their own edges, at any nesting depth.

        Only applies when this row holds exactly one component: only then do
        ducts[0] and ducts[-1] flank the *same* component, so equalizing their
        widths amounts to centering that component's box -- for multiple
        components, ducts[0]/ducts[-1] flank different ones and nothing here
        applies. ducts[0].width_before_vlanes_in() and ducts[-1].width are
        exactly oi_offset and -s_offset (see TopConduitDuct.port_offsets), so
        equalizing those -- not the ducts' raw widths -- leaves port_offsets
        itself untouched and each port sitting exactly on its own lane.

        Returns (leading_first, extra_first, extra_last): leading_first widens
        ducts[0] *before* vlanes_in (trailing space there wouldn't move
        vlanes_in, and so wouldn't move the O_I ports, at all); extra_first/
        extra_last widen each duct's own trailing edge instead, for the
        fallback below.
        """
        if len(self.components) != 1:
            return 0.0, 0.0, 0.0
        first_duct, last_duct = self.conduit_ducts[0], self.conduit_ducts[-1]
        first_duct.calc_layout()
        last_duct.calc_layout()
        if not self.top_conduit_duct.vlanes_in_is_own():
            # Some *other* sender also shares ducts[0].vlanes_in (see
            # vlanes_in_is_own), so shifting where it starts would misalign
            # that sender's own, separately-positioned entering conduit.
            # Fall back to just centering this component's own box (pad
            # whichever duct's raw width is smaller up to the other's),
            # leaving oi_offset be.
            extra_first = max(0.0, last_duct.width - first_duct.width)
            extra_last = max(0.0, first_duct.width - last_duct.width)
            return 0.0, extra_first, extra_last
        # O_I's own index 0 (the one oi_offset aligns to vlanes_in[0]) is
        # already its nearest port to the component's edge, since O_I lays out
        # away from the left edge -- but S lays out *towards* the right edge,
        # so its index 0 (the one s_offset aligns to vlanes_out[0]) ends up
        # farthest from it, and the *last* index nearest instead (see
        # own_s_port_count). Folding that many extra port_margins into the S
        # side's own target closes that gap too, on top of equalizing the two
        # ducts outright.
        bias = self.top_conduit_duct.own_s_port_count() * settings.port_margin
        target = max(first_duct.width_before_vlanes_in(), last_duct.width - bias)
        leading_first = max(0.0, target - first_duct.width_before_vlanes_in())
        extra_last = max(0.0, (target + bias) - last_duct.width)
        return leading_first, 0.0, extra_last

    def align_nesting_levels(self) -> None:
        """Line up the components of all subtimelines at the same nesting depth.

        A subtimeline's components sit owner.height + top_conduit_duct.height below
        the components of the row holding its owner. Per depth, pad each subtimeline's
        top_conduit_duct (via min_top_height) so that this distance is the same
        everywhere: this aligns e.g. the subtimelines of two different components in
        the same row, not just sibling subtimelines of one component.

        Must be called after calc_layout, which must then be called again to apply it.
        """
        rows: list[TimelineBlock] = [self]
        while True:
            pairs = [
                (component, subtl)
                for row in rows
                for component in row.components
                for subtl in component.subtimelines
            ]
            if not pairs:
                break
            offset = max(c.height + s.top_conduit_duct.height for c, s in pairs)
            for component, subtl in pairs:
                subtl.min_top_height = offset - component.height
            rows = [subtl for _, subtl in pairs]

    def calc_layout(self):
        """Calculate the size and layout of the timeline block and its contents."""
        for subtl in self.subtimelines:
            subtl.calc_layout()
        self.top_conduit_duct.calc_layout()
        self.top_conduit_duct.height = max(
            self.top_conduit_duct.height, self.min_top_height
        )

        leading_first, extra_first, extra_last = self._boundary_duct_padding()

        width = 0
        height = 0
        for item in self._iter_cd_and_components():
            item.x = width
            item.y = self.top_conduit_duct.height
            if item is self.conduit_ducts[0]:
                item.calc_layout(leading_pad=leading_first)
                item.width += extra_first
            else:
                item.calc_layout()
            if item is self.conduit_ducts[-1]:
                item.width += extra_last
            width += item.width
            height = max(height, item.height)

        self.width = width
        self.top_conduit_duct.width = self.width
        self.height = (
            self.top_conduit_duct.height
            + max((c.height for c in self.components), default=0)
            + max((tl.height for tl in self.subtimelines), default=0)
        )

    def moveto(self, x: float, y: float) -> None:
        """Move the complete subtimeline by setting a translation filter on the
        containing SVG group."""
        self.transform = svg.Translate(x, y)

    def to_svg(self) -> svg.G:
        """Build the SVG representing this timeline."""
        group = super().to_svg()
        assert group.elements is not None
        # Add sub-elements
        group.elements.extend(item.to_svg() for item in self._iter_cd_and_components())
        group.elements.extend(tl.to_svg() for tl in self.subtimelines)
        group.elements.append(self.top_conduit_duct.to_svg())
        # Set translation
        group.transform = [self.transform]
        return group
