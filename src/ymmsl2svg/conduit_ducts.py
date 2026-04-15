from ymmsl2svg.base import SvgBlock


class TopConduitDuct(SvgBlock):
    """Top conduit duct in a timeline."""

    def __init__(self) -> None:
        super().__init__()
        # Temporary values to test layout
        self.height = 20


class ConduitDuct(SvgBlock):
    """Conduit duct between components in a timeline."""

    def __init__(self) -> None:
        super().__init__()
        # Temporary values to test layout
        self.width = 20
        self.height = 50
