from dataclasses import dataclass

import svg


@dataclass
class SvgBlock:
    """Base class for block visualizations.

    Block visualizations are a rectangle containing further elements.
    This base class creates a group with a rectangle for debugging purposes. Derived
    classes are responsible for creating sub-components and adding those to the group.
    """

    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0

    def to_svg(self) -> svg.Element:
        """Create and return the SVG element to represent this object."""
        rect = svg.Rect(
            class_=[type(self).__name__],
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
        )
        return svg.G(class_=[type(self).__name__ + "_group"], elements=[rect])
