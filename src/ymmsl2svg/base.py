import svg

from ymmsl2svg.settings import settings


class SvgBlock:
    """Base class for block visualizations.

    Block visualizations are a rectangle containing further elements.
    This base class creates a group with a rectangle for debugging purposes. Derived
    classes are responsible for creating sub-components and adding those to the group.
    """

    def __init__(self):
        self.x: float = 0
        """X-position (top-left) of this block"""
        self.y: float = 0
        """Y-position (top-left) of this block"""
        self.width: float = 0
        """Width of this block"""
        self.height: float = 0
        """Height of this block"""

    def to_svg(self) -> svg.G:
        """Create and return the SVG group to represent this object."""
        group = svg.G(class_=[type(self).__name__ + "_group"], elements=[])
        if settings.debug:
            rect = svg.Rect(
                class_=[type(self).__name__],
                x=self.x,
                y=self.y,
                width=self.width,
                height=self.height,
            )
            group.elements = [rect]
        return group
