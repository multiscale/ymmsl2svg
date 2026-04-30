from collections.abc import Iterator
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import (
    Component,
    Conduit,
    Identifier,
    Operator,
    Port,
    Reference,
    Timeline,
)

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.conduit_ducts import ConduitDuct
    from ymmsl2svg.timeline_block import TimelineBlock


def ports_for_operator(component: Component, operator: Operator) -> list[Port]:
    """Return a list of ports of the component for the given operator"""
    return [port for port in component.ports.values() if port.operator is operator]


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
        if len(subtimelines) > 1:
            raise NotImplementedError(
                "Visualization of components with multiple subtimelines "
                "is not yet implemented."
            )

        # Conduit connections to the left/right of this component
        self.left_conduit_duct = left_conduit_duct
        self.right_conduit_duct = right_conduit_duct
        left_conduit_duct.add_right_connector(self)
        right_conduit_duct.add_left_connector(self)
        if subtimelines:  # TODO: support multiple subtimelines
            left_conduit_duct.add_right_connector(subtimelines[0].top_conduit_duct)
            right_conduit_duct.add_left_connector(subtimelines[0].top_conduit_duct)
        # Conduit connections to subtimelines
        for subtl in subtimelines:
            subtl.top_conduit_duct.add_top_component(self)

        # TODO: sequence ports to minimize conduit crossings
        self._ports_per_operator = {
            op: ports_for_operator(component, op) for op in Operator
        }
        self.f_init_ports = self._ports_per_operator[Operator.F_INIT]
        self.o_f_ports = self._ports_per_operator[Operator.O_F]
        self.o_i_ports = self._ports_per_operator[Operator.O_I]
        self.s_ports = self._ports_per_operator[Operator.S]

        self.port_positions: dict[Identifier, tuple[float, float]] = {}
        self.conduits_per_port: dict[Identifier, list[Conduit]] = {
            port: [] for port in self.component.ports
        }

    def add_conduit(self, conduit: Conduit):
        """Register conduit for this component."""
        if conduit.sending_component() == self.component.name:
            portname = conduit.sending_port()
        elif conduit.receiving_component() == self.component.name:
            portname = conduit.receiving_port()
        else:
            raise RuntimeError("Unreachable")
        self.conduits_per_port[portname].append(conduit)

    def conduits_per_operator(
        self,
        operator: Operator,
        timeline: Timeline | None = None,
        reverse: bool = False,
    ) -> Iterator[Conduit]:
        """Iterate over all conduits connected to ports of an operator.

        Args:
            operator: Operator to filter on.
            timeline: Timeline to filter on (only applicable to O_I and S ports).
            reversed: Reverse the order of the conduits.
        """
        ports = self._ports_per_operator[operator]
        if reverse:
            ports = reversed(ports)
        for port in ports:
            # TODO: filter on timeline
            yield from self.conduits_per_port.get(port.name, [])

    def ports_per_operator(
        self,
        operator: Operator,
        timeline: Timeline | None = None,
        reverse: bool = False,
    ) -> Iterator[Reference]:
        """Iterate over full port references of all ports of an operator.

        Args:
            operator: Operator to filter on.
            timeline: Timeline to filter on (only applicable to O_I and S ports).
            reversed: Reverse the order of the ports.
        """
        ports = self._ports_per_operator[operator]
        if reverse:
            ports = reversed(ports)
        for port in ports:
            # TODO: filter on timeline
            yield self.component.name + port.name

    def get_port_position(self, port: Identifier):
        """Get x,y position of the port.

        N.B. F_INIT and O_F ports will return the position w.r.t. the timeline that this
        component is in. O_I and S ports will return the position w.r.t. their
        subtimeline.
        """
        if self.component.ports[port].operator in (Operator.F_INIT, Operator.O_F):
            return self.port_positions[port]
        # Relative to subtimeline:
        return (self.port_positions[port][0] - self.component_x, 0)

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        subtimeline_width = sum(subtl.width for subtl in self.subtimelines)
        text_width = self.estimate_name_width() + 2 * settings.text_margin
        self.component_width = max(
            settings.component_width, subtimeline_width, text_width
        )
        self.width = self.component_width

        # Make space for f_init and o_f ports
        f_init_width = settings.port_size if self.f_init_ports else 0
        o_f_width = settings.port_size if self.o_f_ports else 0
        self.width += f_init_width + o_f_width
        self.component_x = self.x + f_init_width

        max_num_ports = max(len(self.f_init_ports), len(self.o_f_ports))
        port_height = max_num_ports * settings.port_margin
        self.component_height = max(settings.component_height, port_height)
        self.height = self.component_height

        # Make space for o_i and s ports
        if self.o_i_ports or self.s_ports:
            self.height += settings.port_size

        # Calculate (x, y) positions for each port
        for ports, x in [(self.f_init_ports, 0), (self.o_f_ports, self.width)]:
            y = self.component_height / 2 - (len(ports) - 1) * settings.port_margin / 2
            for port in ports:
                self.port_positions[port.name] = (self.x + x, self.y + y)
                y += settings.port_margin

        # TODO: position o_i/s ports correctly for multiple sub-timelines
        for timeline in self.subtimelines:
            oi_offset, s_offset = timeline.top_conduit_duct.port_offsets(self)
            x0 = self.component_x + oi_offset
            for i, port in enumerate(self.o_i_ports):
                x = x0 + (i + 0.5) * settings.port_margin
                self.port_positions[port.name] = (x, self.y + self.height)

            x0 = self.component_x + self.component_width + s_offset
            for i, port in enumerate(self.s_ports):
                x = x0 + (i + 0.5) * settings.port_margin
                self.port_positions[port.name] = (x, self.y + self.height)

            # Move subtimeline
            timeline.moveto(self.component_x, self.y + self.height)

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
        for ports, use_id in [
            (self.f_init_ports, "#port-f_init"),
            (self.o_f_ports, "#port-o_f"),
            (self.o_i_ports, "#port-o_i"),
            (self.s_ports, "#port-s"),
        ]:
            for port in ports:
                x, y = self.port_positions[port.name]
                title = svg.Title(text=str(port.name))
                use = svg.Use(href=use_id, x=x, y=y, elements=[title])
                group.elements.append(use)

        return group
