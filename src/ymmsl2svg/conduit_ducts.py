from ymmsl.v0_2 import Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.settings import settings


class TopConduitDuct(SvgBlock):
    """Top conduit duct in a timeline."""

    def __init__(self, timeline: Timeline) -> None:
        super().__init__()
        self.timeline = timeline

        self.left_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the left."""
        self.right_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the right."""

        self.top_connectors: list[ComponentBlock] = []
        """Parent components connecting to the top."""
        self.bottom_connectors: list[ConduitDuct] = []
        """Conduit ducts connecting to the bottom."""

    def add_bottom_connector(self, connector: "ConduitDuct") -> None:
        self.bottom_connectors.append(connector)

    def add_top_connector(self, connector: ComponentBlock) -> None:
        self.top_connectors.append(connector)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        # Temporary values to test layout
        self.height = 20


class ConduitDuct(SvgBlock):
    """Conduit duct between components in a timeline."""

    _DIRECTION_PRIO = {"LT": 0, "LR": 1, "TR": 2}

    def __init__(self, timeline: Timeline, top_conduit_duct: TopConduitDuct) -> None:
        super().__init__()
        self.timeline = timeline

        self.top_conduit_duct = top_conduit_duct
        """TopConduitDuct connecting to the top of this duct"""
        self.top_conduit_duct.add_bottom_connector(self)

        self.left_connectors: list[ComponentBlock | TopConduitDuct] = []
        """Components and subtimelines connecting to the left of this duct."""
        self.right_connectors: list[ComponentBlock | TopConduitDuct] = []
        """Components and subtimelines connecting to the right of this duct."""
        self.right_components: dict[Reference, ComponentBlock] = {}
        """Map of components to the right of this duct, keyed by their name."""

    def add_left_connector(self, connector: ComponentBlock | TopConduitDuct) -> None:
        """Register a component connecting on the left side of this duct."""
        self.left_connectors.append(connector)
        if isinstance(connector, TopConduitDuct):
            connector.right_conduit_duct = self

    def add_right_connector(self, connector: ComponentBlock | TopConduitDuct) -> None:
        """Register a component connecting on the right side of this duct."""
        self.right_connectors.append(connector)
        if isinstance(connector, TopConduitDuct):
            connector.left_conduit_duct = self
        else:
            self.right_components[connector.component.name] = connector

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        # Temporary values to test layout
        self.width = 20
        self.height = settings.component_height
