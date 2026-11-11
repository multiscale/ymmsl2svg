from collections.abc import Callable
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Component, Conduit, Identifier, Operator

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


class ComponentBlock(SvgBlock):
    """SVG Block that represents a single component in a model, including its ports."""

    def __init__(
        self,
        component: Component,
        subtimelines: list["TimelineBlock"],
        left_conduit_duct: "ConduitDuct",
        right_conduit_duct: "ConduitDuct",
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
        self.subtimelines = subtimelines
        # TODO: support multiple subtimelines. This needs one HorizontalPortGroup per
        # subtimeline (each O_I/S port in the group of its timeline), placed side by
        # side below the component, and a connection to the ducts for each of them.
        if len(subtimelines) > 1:
            raise NotImplementedError(
                "Visualization of components with multiple subtimelines "
                "is not yet implemented."
            )
        subtimeline = subtimelines[0] if subtimelines else None

        # Conduit connections to the left/right of this component
        self.left_conduit_duct = left_conduit_duct
        self.right_conduit_duct = right_conduit_duct
        left_conduit_duct.add_right_connector(self)
        right_conduit_duct.add_left_connector(self)
        if subtimeline is not None:
            left_conduit_duct.add_right_connector(subtimeline.top_conduit_duct)
            right_conduit_duct.add_left_connector(subtimeline.top_conduit_duct)

        # Ports
        self.ports: dict[Identifier, PortBlock] = {
            name: PortBlock(self, port) for name, port in component.ports.items()
        }
        """All ports of this component, by name."""
        self.f_init_group = VerticalPortGroup(self, Operator.F_INIT)
        """F_INIT ports, on the left of the component."""
        self.o_f_group = VerticalPortGroup(self, Operator.O_F)
        """O_F ports, on the right of the component."""
        self.o_i_s_group = HorizontalPortGroup(self, subtimeline)
        """O_I and S ports below the component, connecting to the subtimeline."""

        for port in self.ports.values():
            if port.operator is Operator.F_INIT:
                self.f_init_group.add_port(port)
            elif port.operator is Operator.O_F:
                self.o_f_group.add_port(port)
            else:
                self.o_i_s_group.add_port(port)

    @property
    def port_groups(self) -> list[PortGroup]:
        """All port groups of this component, in drawing order."""
        return [self.f_init_group, self.o_f_group, self.o_i_s_group]

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
        assert first.group is not None
        op = first.operator
        for port in first.group.port_list(first):
            if port.name == port1:
                return -1 if op is Operator.O_F else 1
            elif port.name == port2:
                return 1 if op is Operator.O_F else -1
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

        self.component_height = max(
            settings.component_height,
            self.f_init_group.min_height(),
            self.o_f_group.min_height(),
        )
        self.height = self.component_height

        # The F_INIT and O_F ports go left and right of the component
        for group, x in [
            (self.f_init_group, self.x),
            (self.o_f_group, self.component_x + self.component_width),
        ]:
            group.x, group.y, group.height = x, self.y, self.component_height
            group.calc_layout()

        # The O_I and S ports go right below the component, the subtimeline below them
        self.o_i_s_group.x = self.component_x
        self.o_i_s_group.y = self.y + self.component_height
        self.o_i_s_group.width = self.component_width
        self.o_i_s_group.calc_layout()
        self.height += self.o_i_s_group.height

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
