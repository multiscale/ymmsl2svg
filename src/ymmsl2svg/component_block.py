from ymmsl.v0_2 import Component

from ymmsl2svg.base import SvgBlock


class ComponentBlock(SvgBlock):
    """SVG Block that represents a single component in a model, including its ports."""

    def __init__(self, component: Component) -> None:
        super().__init__()
        self.component = component
        # Temporary values to test layout
        self.width = 100
        self.height = 50
