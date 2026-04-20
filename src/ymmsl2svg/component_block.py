import svg
from ymmsl.v0_2 import Component, Operator, Port

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings


def ports_for_operator(component: Component, operator: Operator) -> list[Port]:
    """Return a list of ports of the component for the given operator"""
    return [port for port in component.ports.values() if port.operator is operator]


class ComponentBlock(SvgBlock):
    """SVG Block that represents a single component in a model, including its ports."""

    def __init__(self, component: Component) -> None:
        super().__init__()
        self.component = component

        # TODO: sequence ports to minimize conduit crossings
        self.f_init_ports = ports_for_operator(component, Operator.F_INIT)
        self.o_f_ports = ports_for_operator(component, Operator.O_F)

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        # Temporary values to test layout
        self.width = 100
        self.height = 50

    def to_svg(self) -> svg.Element:
        """Create and return the SVG element to represent this object."""
        group = super().to_svg()
        assert group.elements is not None
        component = svg.Rect(
            x=self.x + settings.component_border / 2,
            y=self.y + settings.component_border / 2,
            width=self.width - settings.component_border,
            height=self.height - settings.component_border,
            class_=["component"],
            id=f"component.{self.component.name}",
        )
        text = svg.Text(
            text=str(self.component.name),
            x=self.x + self.width / 2,
            y=self.y + self.height / 2,
        )
        group.elements.extend([component, text])
        return group
