from collections.abc import Callable
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Component, Conduit, Identifier, Operator, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.port_groups import (
    HorizontalPortGroup,
    PortBlock,
    PortGroup,
    VerticalPortGroup,
)
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.conduit_ducts import ConduitDuct
    from ymmsl2svg.timeline_block import TimelineBlock
    from ymmsl2svg.timeline_layer import TimelineLayer


class ComponentBlock(SvgBlock):
    """SVG Block that represents a single component in a model, including its ports."""

    def __init__(
        self,
        component: Component,
        layer: "TimelineLayer",
        subtimelines: list["TimelineBlock"],
        left_conduit_duct: "ConduitDuct",
        right_conduit_duct: "ConduitDuct",
        port_timelines: dict[Reference, Timeline],
    ) -> None:
        super().__init__()
        # Geometry
        self.component_x: float = 0
        """x-position of the component rectangle"""
        self.component_width: float = 0
        """Width of the component rectangle"""
        self.component_height: float = 0
        """Height of the component rectangle"""

        # Data
        self.component = component
        self.layer = layer
        """Timeline layer that this component is in."""
        self.subtimelines = subtimelines
        self.port_timelines = port_timelines
        """Timeline (relative to the model) of each O_I and S port, by full port
        reference (see TimelineNode.port_timelines)."""

        # Conduit connections to the left/right of this component
        self.left_conduit_duct = left_conduit_duct
        self.right_conduit_duct = right_conduit_duct
        left_conduit_duct.add_right_connector(self)
        right_conduit_duct.add_left_connector(self)
        # Each subtimeline gets its own slot in the same left/right ducts
        for subtl in subtimelines:
            left_conduit_duct.add_right_connector(subtl.top_conduit_duct)
            right_conduit_duct.add_left_connector(subtl.top_conduit_duct)

        # Ports
        self.f_init_group = VerticalPortGroup(self, Operator.F_INIT)
        """F_INIT ports, on the left of the component."""
        self.o_f_group = VerticalPortGroup(self, Operator.O_F)
        """O_F ports, on the right of the component."""
        self.o_i_s_groups = [HorizontalPortGroup(self, subtl) for subtl in subtimelines]
        """O_I and S ports below the component, one group per subtimeline (in the same
        order as self.subtimelines)."""
        group_per_timeline = {
            group.subtimeline.node.timeline: group for group in self.o_i_s_groups
        }

        self.ports: dict[Identifier, PortBlock] = {}
        """All ports of this component, by name."""

        for port in component.ports.values():
            if port.operator is Operator.F_INIT:
                port_block = self.f_init_group.add_port(port)
            elif port.operator is Operator.O_F:
                port_block = self.o_f_group.add_port(port)
            else:
                timeline = port_timelines[component.name + port.name]
                port_block = group_per_timeline[timeline].add_port(port)
            self.ports[port.name] = port_block

    @property
    def port_groups(self) -> list[PortGroup]:
        """All port groups of this component, in drawing order."""
        return [self.f_init_group, self.o_f_group, *self.o_i_s_groups]

    def add_conduit(self, conduit: Conduit):
        """Register conduit for this component."""
        if conduit.sending_component() == self.component.name:
            portname = conduit.sending_port()
        elif conduit.receiving_component() == self.component.name:
            portname = conduit.receiving_port()
        else:
            raise RuntimeError("Unreachable")
        self.ports[portname].add_conduit(conduit)

    def cmp_ports(self, port1: Identifier, port2: Identifier) -> int:
        """Comparison function for component ports"""
        first = self.ports[port1]
        for port in first.group.ports(first.operator):
            if port.name == port1:
                return -1 if first.operator is Operator.O_F else 1
            elif port.name == port2:
                return 1 if first.operator is Operator.O_F else -1
        raise RuntimeError("Unreachable")

    def sort_output_ports(self, key: Callable) -> None:
        """Sort O_I/O_F ports in this component"""
        for group in self.port_groups:
            group.sort_output_ports(key)

    def sort_input_ports(self, key: Callable) -> None:
        """Sort F_INIT/S ports in this component"""
        for group in self.port_groups:
            group.sort_input_ports(key)

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        subtimeline_width = sum(subtl.width for subtl in self.subtimelines)
        text_width = self.estimate_name_width() + 2 * settings.text_margin
        self.component_width = max(
            settings.component_width, subtimeline_width, text_width
        )
        # Make space for f_init and o_f ports
        self.width = self.component_width + 2 * settings.port_size
        self.component_x = self.x + settings.port_size

        self.component_height = self.minimum_component_height
        self.height = self.component_height

        # The F_INIT and O_F ports go left and right of the component
        for group, x in [
            (self.f_init_group, self.x),
            (self.o_f_group, self.component_x + self.component_width),
        ]:
            group.x, group.y, group.height = x, self.y, self.component_height
            group.calc_layout()

        # The O_I and S ports go right below the component, the subtimelines below
        # them. Each subtimeline (with its port group) gets its own segment.
        bounds = self._segment_bounds()
        for i, port_group in enumerate(self.o_i_s_groups):
            port_group.x = bounds[i]
            port_group.y = self.y + self.component_height
            port_group.width = bounds[i + 1] - bounds[i]
            port_group.calc_layout()
        self.height += self.o_i_s_height

    def _segment_bounds(self) -> list[float]:
        """x-boundaries of each subtimeline's segment: segment i spans [bounds[i],
        bounds[i + 1]), packed left to right by each subtimeline's own width. Leftover
        width goes to the last segment."""
        bounds = [self.component_x]
        for timeline in self.subtimelines[:-1]:
            bounds.append(bounds[-1] + timeline.width)
        bounds.append(self.component_x + self.component_width)
        return bounds

    @property
    def o_i_s_height(self) -> float:
        """Height of the O_I/S port groups below the component rectangle."""
        return max((g.minimum_height for g in self.o_i_s_groups), default=0)

    @property
    def minimum_component_height(self) -> float:
        """Minimum height of the component rectangle, to fit its F_INIT and O_F ports."""
        return max(
            settings.component_height,
            self.f_init_group.minimum_height,
            self.o_f_group.minimum_height,
        )

    def estimate_name_width(self) -> float:
        """Estimate the width (in pixels) of the component's name."""
        # This is intended to slightly overestimate the width (which is prettier than
        # underestimation). Note that the exact size depends on the font used by the SVG
        # viewer, so better estimates (e.g. using pillow to calculate the bounding box)
        # may still be wrong.

        # See `scripts/textsize.py` for character sizes. Assumed font size is 16px.
        # Narrow characters: (3-6 pixels) -> estimate as 5 pixels wide
        # Broad characters: (11+ pixels) -> estimate as 13 pixels wide
        # Medium characters: (8-10 pixels) -> estimate as 10 pixels wide
        lengths = {c: 5 for c in "ijlrIft.[]"} | {c: 13 for c in "BCPRUDGHNOQwmMW"}
        return sum(lengths.get(c, 10) for c in str(self.component.name))

    def to_svg(self) -> svg.G:
        """Build the SVG representing this component and its ports."""
        group = super().to_svg()
        assert group.elements is not None
        component = svg.Rect(
            x=self.component_x + settings.component_border / 2,
            y=self.y + settings.component_border / 2,
            width=self.component_width - settings.component_border,
            height=self.component_height - settings.component_border,
            class_=["component"],
            id=f"component-{self.component.name}",
        )
        text = svg.Text(
            text=str(self.component.name),
            x=self.component_x + self.component_width / 2,
            y=self.y + self.component_height / 2,
        )
        group.elements.extend([component, text])

        # Draw ports
        group.elements.extend(port_group.to_svg() for port_group in self.port_groups)

        return group
