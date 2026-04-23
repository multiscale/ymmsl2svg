from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Component, Identifier, Operator, Port

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
            subtl.top_conduit_duct.add_top_connector(self)

        # TODO: sequence ports to minimize conduit crossings
        self.f_init_ports = ports_for_operator(component, Operator.F_INIT)
        self.o_f_ports = ports_for_operator(component, Operator.O_F)
        self.o_i_ports = ports_for_operator(component, Operator.O_I)
        self.s_ports = ports_for_operator(component, Operator.S)
        self.port_positions: dict[Identifier, tuple[float, float]] = {}

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        subtimeline_width = sum(subtl.width for subtl in self.subtimelines)
        self.component_width = max(settings.component_width, subtimeline_width)
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
        x0 = self.component_x
        for i, port in enumerate(self.o_i_ports):
            x = x0 + (i + 1) * settings.port_margin
            self.port_positions[port.name] = (x, self.y + self.height)
        x0 += self.component_width - len(self.s_ports) * settings.port_margin
        for i, port in enumerate(self.s_ports):
            x = x0 + (i) * settings.port_margin
            self.port_positions[port.name] = (x, self.y + self.height)

        # Move subtimelines
        for timeline in self.subtimelines:
            timeline.moveto(self.component_x, self.y + self.height)

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
