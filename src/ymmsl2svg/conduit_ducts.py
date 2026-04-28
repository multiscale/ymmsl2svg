from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

import svg
from ymmsl.v0_2 import Conduit, Identifier, Operator, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.settings import settings


@dataclass
class Lane:
    """Horizontal or vertical lane for conduits."""

    horizontal: bool
    """True if this is a horizontal lane, False if this is a vertical lane."""
    pos: float | None = None
    """x or y coordinate of the lane."""


class Lanes:
    """Bundle of horizontal/vertical lanes, indexed by Conduit.sender."""

    def __init__(self, horizontal: bool, reversed: bool = False) -> None:
        """Create a new bundle of lanes

        Args:
            horizontal: Direction of conduits in this lane: True if horizontal, False if
                vertical.
            reversed: By default lanes are drawn top -> bottom (or left -> right) in the
                order they were added. Setting this to True will reverse the order.
        """
        self._horizontal = horizontal
        self._reversed = reversed
        self._lanes: dict[Reference, Lane] = {}

    def set_pos(self, offset: float, spacing: float) -> float:
        """Set the x/y coordinates for all lanes in this bundle."""
        for i, lane in enumerate(self):
            lane.pos = offset + i * spacing
        return len(self) * spacing

    def __iter__(self) -> Iterator[Lane]:
        """Get an iterator over the Lanes in this bundle."""
        if self._reversed:
            return reversed(self._lanes.values())
        return iter(self._lanes.values())

    def __getitem__(self, sender: Reference) -> Lane:
        """Get (and create if required) the Lane for this sender."""
        return self._lanes.setdefault(sender, Lane(self._horizontal))

    def __len__(self) -> int:
        """Get number of lanes in this bundle."""
        return len(self._lanes)


class Point:
    """Abstract class for providing the start / end point of a ConduitRoute."""

    def __call__(self) -> tuple[float, float]:
        """Returns the (x, y) coordinate of this points."""
        raise NotImplementedError()


class PortPoint(Point):
    """Point corresponding to a component's port."""

    def __init__(self, component: ComponentBlock, port: Identifier):
        self.component = component
        """Component the port belongs to"""
        self.port = port
        """Port of the component"""

    def __call__(self) -> tuple[float, float]:
        return self.component.get_port_position(self.port)


@dataclass
class ConduitRoute:
    """Route of a conduit through the conduit ducts in this timeline."""

    origin: Point
    """Origin point in this timeline."""
    destination: Point
    """Destination point in this timeline."""
    lanes: list[Lane]
    """Lanes visited (in order) between origin and destination"""

    def to_svg(self) -> svg.Path:
        """Create an SVG Path to describe this conduit route."""
        x, y = self.origin()
        path: list[svg.PathData] = [svg.M(x, y)]
        for lane in self.lanes:
            if lane.horizontal:
                y = lane.pos
                assert y is not None
                path.append(svg.V(y))
            else:
                x = lane.pos
                assert x is not None
                path.append(svg.H(x))
        assert self.lanes
        x, y = self.destination()
        if lane.horizontal:
            path.append(svg.H(x))
            path.append(svg.V(y))
        else:
            path.append(svg.V(y))
            path.append(svg.H(x))
        return svg.Path(d=path, class_=["conduit"])


class TopConduitDuct(SvgBlock):
    """Top conduit duct in a timeline."""

    def __init__(self, timeline: Timeline) -> None:
        super().__init__()
        self.timeline = timeline

        self.left_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the left."""
        self.right_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the right."""

        self.top_components: list[ComponentBlock] = []
        """Parent components connecting to the top."""
        self.ducts: list[ConduitDuct] = []
        """Conduit ducts connecting to the bottom."""

        self._destinations: dict[Reference, tuple[Literal["T", "B"], int]] = {}
        """Destination (top/bottom connectors) and index in the list, per component"""

        # Horizontal lanes for routing conduits:
        self._hlanes_for_s = Lanes(horizontal=True)
        """Horizontal lanes for conduits going to S ports."""
        self._hlanes_for_oi = Lanes(horizontal=True)
        """Horizontal lanes for conduits going to O_I ports."""
        self._hlanes = Lanes(horizontal=True)
        """Main horizontal lanes, for all conduits going left -> right."""

        self._routes: list[ConduitRoute] = []
        """List of conduit routes through this timeline."""

    def add_conduit_duct(self, conduit_duct: "ConduitDuct") -> None:
        """Register conduit duct."""
        self.ducts.append(conduit_duct)

    def add_top_component(self, component: ComponentBlock) -> None:
        """Register parent component connecting to the top"""
        self.top_components.append(component)

    def _fill_destinations(self) -> None:
        """Build lookup map for component destinations."""
        for i, comp in enumerate(self.top_components):
            self._destinations[comp.component.name] = ("T", i)
        for i, duct in enumerate(self.ducts):
            dest = ("B", i)
            for destination in duct.destinations():
                self._destinations[destination] = dest

    def destinations(self) -> Iterable[Reference]:
        """Get components that are inside this timeline or a subtimeline."""
        if not self._destinations:
            self._fill_destinations()
        return self._destinations.keys()

    def _get_input_conduits(self) -> Iterator[tuple[int, Point, Conduit]]:
        """Iterator over all input conduits.

        This iterator yields all conduits coming from:
        - Top components (component_index >= 0)
        - Parent timeline (component_index == -1)

        Yields:
            (component_index, origin_point, conduit) for each conduit, starting with
            conduits connected to the right-most port.
        """
        idx = len(self.top_components)
        for component in reversed(self.top_components):
            idx -= 1
            for conduit in component.conduits_per_operator(
                Operator.O_I, self.timeline, reverse=True
            ):
                origin = PortPoint(component, conduit.sending_port())
                yield (idx, origin, conduit)
        # TODO: yield conduits coming from parent timeline

    def _get_duct_conduits(self) -> Iterator[tuple[int, Point, Conduit]]:
        """Iterator over all conduits connected to ConduitDucts.

        Yields:
            (duct_index, origin_point, conduit) for each conduit, starting with
            conduits connected to the right-most port.
        """
        for idx, duct in enumerate(self.ducts):
            for origin, conduit in duct.get_conduits():
                yield (idx, origin, conduit)

    def route_conduits(self) -> None:
        """Route all conduits inside this timeline and all subtimelines."""
        if not self._destinations:
            self._fill_destinations()

        if self.top_components:
            # Reserve space for all O_I ports in the first component
            for port in self.top_components[0].ports_per_operator(
                Operator.O_I, self.timeline, reverse=True
            ):
                self.ducts[0].vlanes_in[port]
            # TODO: Reserve space for all S ports in the last component
            for port in self.top_components[-1].ports_per_operator(
                Operator.S, self.timeline
            ):
                port_id = port[-1]
                assert isinstance(port_id, Identifier)
                conduits = self.top_components[-1].conduits_per_port[port_id]
                if conduits:
                    assert len(conduits) == 1  # S port can have at most 1 conduit
                    self.ducts[-1].vlanes_out[conduits[0].sender]
                else:
                    # No conduits connected to this port, but still reserve space:
                    self.ducts[-1].vlanes_out[port]

        # Route conduits coming from top components and parent timelines
        for itop, origin, conduit in self._get_input_conduits():
            lanes = []
            if itop > 0:
                lanes.append(self._hlanes_for_oi[conduit.sender])
            destination = self._destinations.get(conduit.receiving_component())
            if destination is None:
                continue  # TODO
                # Go to right_conduit_duct:
                # lanes.append(self.ducts[0].vlanes_in[conduit.sender])
                # lanes.append(self._hlanes[conduit.sender])
                # lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
            elif destination[0] == "B":
                # Go to the correct duct at the bottom
                lanes.append(self.ducts[0].vlanes_in[conduit.sender])
                idest = destination[1]
                if idest > 0:
                    lanes.append(self._hlanes[conduit.sender])
                    lanes.append(self.ducts[idest].vlanes_in[conduit.sender])
                dest = self.ducts[idest].get_point_for(conduit)
            elif destination[0] == "T":
                continue  # TODO, interact coupling
            route = ConduitRoute(origin, dest, lanes)
            self._routes.append(route)

        # Route conduits coming from internal componetns and subtimelines
        for iduct, origin, conduit in self._get_duct_conduits():
            destination = self._destinations.get(conduit.receiving_component())
            lanes = []
            if destination is None:
                continue  # TODO
            elif destination[0] == "B":
                idest = destination[1]
                if iduct == idest:
                    lanes.append(self.ducts[iduct].vlanes_transfer[conduit.sender])
                else:
                    lanes.append(self.ducts[iduct].vlanes_out[conduit.sender])
                    lanes.append(self._hlanes[conduit.sender])
                    lanes.append(self.ducts[idest].vlanes_in[conduit.sender])
                dest = self.ducts[idest].get_point_for(conduit)
            else:  # destination is Top
                idest = destination[1]
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[conduit.sender])
                    lanes.append(self._hlanes[conduit.sender])
                lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
                if idest != len(self.top_components) - 1:
                    lanes.append(self._hlanes_for_s[conduit.sender])
                dest = PortPoint(self.top_components[idest], conduit.receiving_port())
            route = ConduitRoute(origin, dest, lanes)
            self._routes.append(route)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        offset = settings.conduit_margin / 2
        height = self._hlanes_for_s.set_pos(offset, settings.conduit_margin)
        height += self._hlanes_for_oi.set_pos(offset + height, settings.conduit_margin)
        height += self._hlanes.set_pos(offset + height, settings.conduit_margin)
        if height:
            height += settings.conduit_margin
        self.height = height

    def to_svg(self) -> svg.G:
        group = super().to_svg()
        assert group.elements is not None
        for route in self._routes:
            group.elements.append(route.to_svg())
        return group


class ConduitDuct(SvgBlock):
    """Conduit duct between components in a timeline."""

    def __init__(self, top_conduit_duct: TopConduitDuct) -> None:
        super().__init__()

        self.top_conduit_duct = top_conduit_duct
        """TopConduitDuct connecting to the top of this duct"""
        self.top_conduit_duct.add_conduit_duct(self)

        self.left_connectors: list[ComponentBlock | TopConduitDuct] = []
        """Components and subtimelines connecting to the left of this duct."""
        self.right_connectors: list[ComponentBlock | TopConduitDuct] = []
        """Components and subtimelines connecting to the right of this duct."""

        self._destinations: dict[Reference, int] = {}
        """Index in the self.right_connectors, per destination component"""

        self.vlanes_out = Lanes(horizontal=False)
        """Vertical lanes, carrying conduits from left to top."""
        self.vlanes_transfer = Lanes(horizontal=False)
        """Vertical lanes, carrying conduits from left to right."""
        self.vlanes_in = Lanes(horizontal=False, reversed=True)
        """Vertical lanes, carrying conduits from top to right."""

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

    def destinations(self) -> Iterable[Reference]:
        """Return all components reachable through this duct."""
        if not self._destinations:
            for i, connector in enumerate(self.right_connectors):
                if isinstance(connector, ComponentBlock):
                    self._destinations[connector.component.name] = i
                else:
                    for destination in connector.destinations():
                        self._destinations.setdefault(destination, i)
        return self._destinations.keys()

    def get_point_for(self, conduit: Conduit) -> Point:
        """Get a Point to describe the position of the destination of the conduit."""
        idx = self._destinations[conduit.receiving_component()]
        connector = self.right_connectors[idx]
        if isinstance(connector, ComponentBlock):
            return PortPoint(connector, conduit.receiving_port())
        else:
            raise NotImplementedError()  # TODO

    def get_conduits(self) -> Iterator[tuple[Point, Conduit]]:
        """Iterator over all conduits that enter this conduit duct from left_connectors.

        Yields:
            (origin_point, conduit) for each conduit.
        """
        for left_connector in self.left_connectors:
            if isinstance(left_connector, ComponentBlock):
                for conduit in left_connector.conduits_per_operator(Operator.O_F):
                    origin = PortPoint(left_connector, conduit.sending_port())
                    yield origin, conduit
            else:
                left_connector.route_conduits()
                # TODO: loop over all conduits coming into this timeline...

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        # The first and last ConduitDuct use port_margin for spacing, so the lanes align
        # with the O_I / S ports of the component above it.
        lane_width = settings.conduit_margin
        if not self.left_connectors or not self.right_connectors:
            lane_width = settings.port_margin

        offset = self.x + lane_width / 2
        width = self.vlanes_out.set_pos(offset, lane_width)
        width += self.vlanes_transfer.set_pos(offset + width, lane_width)
        width += self.vlanes_in.set_pos(offset + width, lane_width)
        self.width = width

        # Only for debug visualization, our conduits can extend below
        self.height = settings.component_height
