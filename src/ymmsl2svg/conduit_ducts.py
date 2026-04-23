import html
from dataclasses import dataclass
from itertools import groupby

import svg
from ymmsl.v0_2 import Conduit, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.settings import settings


@dataclass
class ConduitRoute:
    conduit: Conduit
    origin: str
    origin_idx: int
    destination: str
    destination_idx: int
    # Only used by TopConduitDuct:
    passthrough_tcd: bool = False

    def __str__(self) -> str:
        return ", ".join(
            f"{name}={getattr(self, name)}" for name in self.__dataclass_fields__
        )


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

        self.conduit_routes: list[ConduitRoute] = []

    def add_bottom_connector(self, connector: "ConduitDuct") -> None:
        self.bottom_connectors.append(connector)

    def add_top_connector(self, connector: ComponentBlock) -> None:
        self.top_connectors.append(connector)

    def accepts_subtimeline(self, timeline: Timeline) -> bool:
        """Determine if we can route conduits going to the provided timeline"""
        return timeline == self.timeline or self.timeline.is_subtimeline(timeline)

    def _get_destination(
        self, conduit: Conduit, destination_timeline: Timeline
    ) -> tuple[str, int]:
        """Determine destination of this conduit"""
        if destination_timeline == self.timeline:
            receiver = conduit.receiving_component()
            # Maybe a parent component at the top
            for idx, c in enumerate(self.top_connectors):
                if c.component.name == receiver:
                    return ("T", idx)
            # Or a child componenent at the bottom
            for idx, duct in enumerate(self.bottom_connectors):
                if receiver in duct.right_components:
                    return ("B", idx)
            # Must be a model port
            assert self.right_conduit_duct is None
            return ("R", 0)

        if self.timeline.is_subtimeline(destination_timeline):
            # Route to the bottom
            for idx, duct in enumerate(self.bottom_connectors):
                if duct.accepts_subtimeline(destination_timeline):
                    return ("B", idx)

        # Route to the right
        assert self.right_conduit_duct is not None
        return ("R", 0)

    def _do_route(
        self,
        conduit: Conduit,
        destination_timeline: Timeline,
        origin_direction: str,
        origin_idx: int,
    ) -> None:
        """Shared logic for routing a conduit through this duct."""
        direction, idx = self._get_destination(conduit, destination_timeline)
        route = ConduitRoute(conduit, origin_direction, origin_idx, direction, idx)
        self._set_passthrough(route)
        self.conduit_routes.append(route)
        if direction == "R" and self.right_conduit_duct is not None:
            self.right_conduit_duct.route_from_left(conduit, destination_timeline, self)
        if direction == "B":
            duct = self.bottom_connectors[idx]
            duct.route_from_top(conduit, destination_timeline)

    def _set_passthrough(self, route: ConduitRoute):
        """Determine if this conduit route just passes through vertically"""
        route.passthrough_tcd = (
            route.origin == "T"
            and route.origin_idx == 0
            and route.destination == "B"
            and route.destination_idx == 0
        ) or (
            route.origin == "B"
            and route.origin_idx == len(self.bottom_connectors) - 1
            and route.destination == "T"
            and route.destination_idx == len(self.top_connectors) - 1
        )

    def route_from_top(
        self, conduit: Conduit, destination_timeline: Timeline, origin: ComponentBlock
    ) -> None:
        """Route a conduit originating from a component above the TopConduitDuct."""
        origin_idx = self.top_connectors.index(origin)
        self._do_route(conduit, destination_timeline, "T", origin_idx)

    def route_from_bottom(
        self, conduit: Conduit, destination_timeline: Timeline, origin: "ConduitDuct"
    ) -> None:
        """Route a conduit originating from a component below the TopConduitDuct."""
        origin_idx = self.bottom_connectors.index(origin)
        self._do_route(conduit, destination_timeline, "B", origin_idx)

    def route_from_left(self, conduit: Conduit, destination_timeline: Timeline) -> None:
        """Route a conduit originating from a component left of the TopConduitDuct."""
        self._do_route(conduit, destination_timeline, "L", 0)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        num_lanes = len(
            {
                route.conduit.sender
                for route in self.conduit_routes
                if not route.passthrough_tcd
            }
        )
        # Temporary values to test layout
        self.height = num_lanes * settings.conduit_margin

    def to_svg(self) -> svg.G:
        group = super().to_svg()
        assert group.elements is not None
        if settings.debug:
            txt = "Conduits going through this duct:\n"
            txt += "\n".join(str(c) for c in self.conduit_routes)
            group.elements.append(svg.Title(text=html.escape(txt)))
        return group


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

        self.conduit_routes: list[ConduitRoute] = []
        """List of conduits routed through this duct"""

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

    def accepts_subtimeline(self, timeline: Timeline) -> bool:
        """Determine if we can route conduits going to the provided timeline"""
        return self._get_subtimeline_connector_idx(timeline) is not None

    # TODO: Maybe cache?
    def _get_subtimeline_connector_idx(self, timeline: Timeline) -> int | None:
        if not self.timeline.is_subtimeline(timeline):
            return None
        for i, connector in enumerate(self.right_connectors):
            if isinstance(connector, TopConduitDuct) and connector.accepts_subtimeline(
                timeline
            ):
                return i
        return None

    def route_from_left(
        self,
        conduit: Conduit,
        destination_timeline: Timeline,
        origin: ComponentBlock | TopConduitDuct,
    ) -> None:
        """Route a conduit originating from a component left of the duct."""
        # Figure out where this conduit should go
        origin_idx = self.left_connectors.index(origin)
        if destination_timeline == self.timeline:
            right_component = self.right_components.get(conduit.receiving_component())
            if right_component is not None:
                # We connect on the right side to the receiver
                destination_idx = self.right_connectors.index(right_component)
                self.conduit_routes.append(
                    ConduitRoute(conduit, "L", origin_idx, "R", destination_idx)
                )
                return  # Done
        else:
            connector_idx = self._get_subtimeline_connector_idx(destination_timeline)
            if connector_idx is not None:
                self.conduit_routes.append(
                    ConduitRoute(conduit, "L", origin_idx, "R", connector_idx)
                )
                tcd = self.right_connectors[connector_idx]
                assert isinstance(tcd, TopConduitDuct)
                tcd.route_from_left(conduit, destination_timeline)
                return  # Done

        # In all other cases we need to route to the topconduitduct
        self.conduit_routes.append(ConduitRoute(conduit, "L", origin_idx, "T", 0))
        self.top_conduit_duct.route_from_bottom(conduit, destination_timeline, self)

    def route_from_top(self, conduit: Conduit, destination_timeline: Timeline) -> None:
        """Route a conduit originating from a component above the duct."""
        # Should route to a component or subtimeline
        right_component = self.right_components.get(conduit.receiving_component())
        if right_component is not None:
            destination_idx = self.right_connectors.index(right_component)
            self.conduit_routes.append(
                ConduitRoute(conduit, "T", 0, "R", destination_idx)
            )
            return  # Done
        connector_idx = self._get_subtimeline_connector_idx(destination_timeline)
        assert connector_idx is not None
        self.conduit_routes.append(ConduitRoute(conduit, "T", 0, "R", connector_idx))
        tcd = self.right_connectors[connector_idx]
        assert isinstance(tcd, TopConduitDuct)
        tcd.route_from_left(conduit, destination_timeline)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        num_lanes = len({route.conduit.sender for route in self.conduit_routes})
        if self.left_connectors and self.right_connectors:
            self.width = num_lanes * settings.conduit_margin
        else:  # Special case for first / last duct
            self.width = num_lanes * max(settings.conduit_margin, settings.port_margin)

        # Temporary values to test layout
        self.width = 20
        self.height = settings.component_height

    def to_svg(self) -> svg.G:
        group = super().to_svg()
        assert group.elements is not None
        if settings.debug:
            txt = "Conduits going through this duct:\n"
            txt += "\n".join(str(c) for c in self.conduit_routes)
            group.elements.append(svg.Title(text=html.escape(txt)))
        return group
