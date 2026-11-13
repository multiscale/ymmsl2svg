from collections.abc import Callable
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Conduit, Identifier, Operator, Port, Reference

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.component_block import ComponentBlock
    from ymmsl2svg.timeline_block import TimelineBlock


class PortBlock:
    """A single port of a component: its yMMSL Port, its conduits and its position."""

    _HREF = {
        Operator.F_INIT: "#port-f_init",
        Operator.O_F: "#port-o_f",
        Operator.O_I: "#port-o_i",
        Operator.S: "#port-s",
    }

    def __init__(self, group: "PortGroup", port: Port) -> None:
        self.group = group
        """Port group this port is in."""
        self.port = port
        """The yMMSL port."""
        self.conduits: list[Conduit] = []
        """Conduits connected to this port."""
        self.x: float = 0
        """x-position of the port, see class docs for the frame."""
        self.y: float = 0
        """y-position of the port, see class docs for the frame."""

    @property
    def component(self) -> "ComponentBlock":
        """Component this port belongs to."""
        return self.group.component

    @property
    def name(self) -> Identifier:
        """Name of the port."""
        return self.port.name

    @property
    def operator(self) -> Operator:
        """Operator of the port."""
        return self.port.operator

    @property
    def ref(self) -> Reference:
        """Full reference to the port: component name + port name."""
        return self.component.component.name + self.port.name

    @property
    def position(self) -> tuple[float, float]:
        """(x, y) position of the port, see class docs for the frame."""
        return (self.x, self.y)

    def add_conduit(self, conduit: Conduit) -> None:
        """Register a conduit connected to this port."""
        self.conduits.append(conduit)

    def to_svg(self, dx: float = 0, dy: float = 0) -> svg.Use:
        """Build the SVG representing this port.

        Args:
            dx, dy: Offset from the frame of the port position to the frame of the SVG
                group that this port is drawn in.
        """
        return svg.Use(
            href=self._HREF[self.operator],
            x=self.x + dx,
            y=self.y + dy,
            elements=[svg.Title(text=str(self.name))],
        )


class VerticalPortGroup(SvgBlock):
    """The F_INIT or the O_F ports of a component, in a column on one of its sides."""

    def __init__(self, component: "ComponentBlock", operator: Operator) -> None:
        super().__init__()
        if operator not in (Operator.F_INIT, Operator.O_F):
            raise ValueError(f"A VerticalPortGroup has no {operator.name} ports")
        self.component = component
        """Component that these ports belong to."""
        self.operator = operator
        """Operator of the ports: F_INIT (left side) or O_F (right side)."""
        self._ports: list[PortBlock] = []
        """Ports, from top to bottom."""

    def add_port(self, port: Port) -> None:
        """Add a port to this group."""
        assert port.operator is self.operator
        self._ports.append(PortBlock(self, port))

    def ports(self, operator: Operator, reverse: bool = False) -> list[PortBlock]:
        """Get the ports of an operator (F_INIT or O_F), from top to bottom.

        Args:
            operator: Operator of the ports, must be the operator of this group.
            reverse: Return the ports from bottom to top instead. N.B. the result is
                then a copy and not the list of this group.
        """
        if operator is not self.operator:
            raise ValueError(
                f"This VerticalPortGroup has {self.operator.name} ports, not "
                f"{operator.name}"
            )
        return self._ports[::-1] if reverse else self._ports

    def sort_output_ports(self, key: Callable) -> None:
        """Sort O_F ports by key(conduits of the port), from bottom to top."""
        if self.operator is Operator.O_F:
            self._ports.sort(key=lambda port: key(port.conduits), reverse=True)

    def sort_input_ports(self, key: Callable) -> None:
        """Sort F_INIT ports by key(conduits of the port), from top to bottom."""
        if self.operator is Operator.F_INIT:
            self._ports.sort(key=lambda port: key(port.conduits))

    def min_height(self) -> float:
        """Minimal height to fit all ports."""
        return len(self._ports) * settings.port_margin

    def calc_layout(self) -> None:
        """Position the ports.

        self.x, self.y and self.height must be set by the ComponentBlock first.
        """
        self.width = settings.port_size
        # Ports are drawn on the outer edge, away from the component
        x = self.x if self.operator is Operator.F_INIT else self.x + self.width
        y = self.y + self.height / 2 - (len(self._ports) - 1) * settings.port_margin / 2
        for port in self._ports:
            port.x, port.y = x, y
            y += settings.port_margin

    def to_svg(self) -> svg.G:
        """Build the SVG representing these ports."""
        group = super().to_svg()
        assert group.elements is not None
        group.elements.extend(port.to_svg() for port in self._ports)
        return group


class HorizontalPortGroup(SvgBlock):
    """The O_I and S ports of a component that send/receive in one of its subtimelines.

    A component without a subtimeline has no O_I or S ports, and its group stays empty
    (zero height, nothing drawn).
    """

    def __init__(
        self, component: "ComponentBlock", subtimeline: "TimelineBlock | None"
    ) -> None:
        super().__init__()
        self.component = component
        """Component that these ports belong to."""
        self.subtimeline = subtimeline
        """Subtimeline that these ports send/receive in, None if there is none."""
        self.o_i_ports: list[PortBlock] = []
        """O_I ports, from left to right."""
        self.s_ports: list[PortBlock] = []
        """S ports, from left to right."""
        if subtimeline is not None:
            subtimeline.top_conduit_duct.add_port_group(self)

    @property
    def name(self) -> Reference:
        """Name of the component that these ports belong to."""
        return self.component.component.name

    def add_port(self, port: Port) -> None:
        """Add an O_I or S port to this group."""
        # yMMSL gives every component with O_I/S ports a subtimeline
        assert self.subtimeline is not None
        self.ports(port.operator).append(PortBlock(self, port))

    def ports(self, operator: Operator, reverse: bool = False) -> list[PortBlock]:
        """Get the ports of an operator (O_I or S), from left to right.

        Args:
            operator: Operator of the ports.
            reverse: Return the ports from right to left instead. N.B. the result is
                then a copy and not the list of this group.
        """
        if operator is Operator.O_I:
            ports = self.o_i_ports
        elif operator is Operator.S:
            ports = self.s_ports
        else:
            raise ValueError(f"A HorizontalPortGroup has no {operator.name} ports")
        return ports[::-1] if reverse else ports

    def sort_output_ports(self, key: Callable) -> None:
        """Sort the O_I ports by key(conduits of the port), from left to right."""
        self.o_i_ports.sort(key=lambda port: key(port.conduits))

    def sort_input_ports(self, key: Callable) -> None:
        """Sort the S ports by key(conduits of the port), from left to right."""
        self.s_ports.sort(key=lambda port: key(port.conduits))

    def calc_layout(self) -> None:
        """Position the ports and move the subtimeline right below this group.

        self.x, self.y and self.width must be set by the ComponentBlock first.
        """
        if self.subtimeline is None:
            self.height = 0
            return
        self.height = settings.port_size
        oi_offset, s_offset = self.subtimeline.top_conduit_duct.port_offsets(self)
        for ports, x0 in [
            (self.o_i_ports, oi_offset),
            (self.s_ports, self.width + s_offset),
        ]:
            for i, port in enumerate(ports):
                port.x = x0 + (i + 0.5) * settings.port_margin
                port.y = 0
        self.subtimeline.moveto(self.x, self.y + self.height)

    def to_svg(self) -> svg.G:
        """Build the SVG representing these ports."""
        group = super().to_svg()
        assert group.elements is not None
        # From the frame of the subtimeline to the frame of the component's timeline:
        dx, dy = self.x, self.y + self.height
        for port in self.o_i_ports + self.s_ports:
            group.elements.append(port.to_svg(dx, dy))
        return group


PortGroup = VerticalPortGroup | HorizontalPortGroup
"""Any group of ports of a component."""
