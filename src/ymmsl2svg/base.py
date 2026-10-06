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
                class_=[type(self).__name__, "debug"],
                x=self.x,
                y=self.y,
                width=self.width,
                height=self.height,
            )
            group.elements = [rect]
        return group


def split_debug_layer(group: svg.G) -> svg.G | None:
    """Move the debug rectangles (see SvgBlock.to_svg) out of `group` into a separate
    layer, with the same nesting and transformations so they keep their position.

    Drawing the returned layer before `group` puts all debug rectangles below
    everything else. Otherwise the rectangle of one block can hide conduits drawn by
    another block before it. The model's background is moved along, as it would
    otherwise hide all debug rectangles inside the model.

    Returns:
        The layer, or None if `group` has no debug rectangles.
    """
    layer: list[svg.Element] = []
    kept: list[svg.Element] = []
    for element in group.elements or []:
        classes = (element.class_ or []) if isinstance(element, svg.Rect) else []
        if "debug" in classes or "model" in classes:
            layer.append(element)
            continue
        kept.append(element)
        if isinstance(element, svg.G):
            sublayer = split_debug_layer(element)
            if sublayer is not None:
                layer.append(sublayer)
    group.elements = kept
    if not layer:
        return None
    return svg.G(transform=group.transform, elements=layer)
