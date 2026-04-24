from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

import svg
from ymmsl.v0_2 import Conduit, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.settings import settings


@dataclass
class HLane:
    """Horizontal lane for conduits"""

    y: float | None = None
    """y-coordinate of the lane"""


@dataclass
class VLane:
    """Vertical lane for conduits"""

    x: float | None = None
    """x-coordinate of the lane"""


class HLanes:
    """Bundle of horizontal lanes, indexed by Conduit.sender."""

    def __init__(self) -> None:
        self._lanes: dict[Reference, HLane] = {}

    def set_y(self, y_offset: float, spacing: float) -> float:
        """Set the y coordinates for all lanes in this bundle."""
        for i, lane in enumerate(self._lanes.values()):
            lane.y = y_offset + i * spacing
        return len(self) * spacing

    def __getitem__(self, sender: Reference) -> HLane:
        """Get (and create if required) the HLane for this sender."""
        return self._lanes.setdefault(sender, HLane())

    def __len__(self) -> int:
        """Get number of lanes in this bundle."""
        return len(self._lanes)


class VLanes:
    """Bundle of vertical lanes, indexed by Conduit.sender."""

    def __init__(self, reverse: bool = False) -> None:
        self._reverse = reverse
        self._lanes: dict[Reference, VLane] = {}

    def set_x(self, x_offset: float, spacing: float) -> float:
        """Set the x coordinates for all lanes in this bundle."""
        for i, lane in enumerate(self._lanes.values()):
            lane.x = x_offset + i * spacing
        return len(self) * spacing

    def __getitem__(self, sender: Reference) -> VLane:
        """Get (and create if required) the HLane for this sender."""
        return self._lanes.setdefault(sender, VLane())

    def __len__(self) -> int:
        """Get number of lanes in this bundle."""
        return len(self._lanes)


class Point:
    """Abstract class for providing the start / end point of a ConduitRoute."""

    def __call__(self) -> tuple[float, float]:
        raise NotImplementedError()


class PortPoint(Point):
    """Point corresponding to a component's port."""

    def __init__(self, component: ComponentBlock, port: Reference, is_parent: bool):
        self.component = component
        """Component the port belongs to"""
        self.port = port
        """Port of the component"""
        self.is_parent = is_parent
        """Whether this point is for a parent component"""

    def __call__(self) -> tuple[float, float]:
        x, y = self.component.get_port_position(self.port)
        if self.is_parent:
            return x - self.component.component_x, 0
        return x, y


@dataclass
class ConduitRoute:
    """Route of a conduit through the conduit ducts in this timeline."""

    origin: Point
    """Origin point in this timeline."""
    destination: Point
    """Destination point in this timeline."""
    lanes: list[HLane | VLane]
    """Lanes visited (in order) between origin and destination"""

    def to_svg(self) -> svg.Path:
        """Create an SVG Path to describe this conduit route."""
        x, y = self.origin()
        path: list[svg.PathData] = [svg.M(x, y)]
        for lane in self.lanes:
            if isinstance(lane, HLane):
                y = lane.y
                assert y is not None
                path.append(svg.V(y))
            else:
                x = lane.x
                assert x is not None
                path.append(svg.H(x))
        assert self.lanes
        x, y = self.destination()
        if isinstance(lane, HLane):
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
        self._routes: list[ConduitRoute] = []

        self._hlanes_for_s = HLanes()
        """Horizontal lanes for conduits going to S ports."""
        self._hlanes_for_oi = HLanes()
        """Horizontal lanes for conduits going to O_I ports."""
        self._hlanes = HLanes()
        """Main horizontal lanes, for all conduits going left -> right."""

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

    def _top_conduits_for_component(
        self, comp: ComponentBlock, first: bool
    ) -> Iterator[tuple[Point, Conduit]]:
        """Helper method to iterate over conduits coming from this component."""
        for port, conduits in comp.conduits_per_oi_port(self.timeline):
            if first:
                # Reserve a vertical lane, even if the port is not connected
                self.ducts[0].vlanes_in[port]
            origin = PortPoint(comp, port, True)
            for conduit in conduits:
                yield origin, conduit

    def route_conduits(self) -> None:
        """Route all conduits inside this timeline and all subtimelines."""
        if not self._destinations:
            self._fill_destinations()

        # TODO: collect from left_conduit_duct
        # Collect from top components
        for i, comp in enumerate(self.top_components):
            for origin, conduit in self._top_conduits_for_component(comp, i == 0):
                lanes = []
                if i > 0:
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
                    idx = destination[1]
                    if idx > 0:
                        lanes.append(self._hlanes[conduit.sender])
                        lanes.append(self.ducts[idx].vlanes_in[conduit.sender])
                    dest = self.ducts[idx].get_point_for(conduit)
                elif destination[0] == "T":
                    continue  # TODO
                route = ConduitRoute(origin, dest, lanes)
                self._routes.append(route)
        # reserve lanes for S ports on last component

        # Collect from conduit ducts
        last_duct_idx = len(self.ducts) - 1
        for i, duct in enumerate(self.ducts):
            for origin, conduit in duct.get_conduits():
                destination = self._destinations.get(conduit.receiving_component())
                lanes = []
                if destination is None:
                    continue  # TODO
                elif destination[0] == "B":
                    idx = destination[1]
                    if idx == i:
                        lanes.append(duct.vlanes_transfer[conduit.sender])
                    else:
                        lanes.append(duct.vlanes_out[conduit.sender])
                        lanes.append(self._hlanes[conduit.sender])
                        lanes.append(self.ducts[idx].vlanes_in[conduit.sender])
                    dest = self.ducts[idx].get_point_for(conduit)
                else:  # destination is Top
                    idx = destination[1]
                    if i != last_duct_idx:
                        lanes.append(duct.vlanes_out[conduit.sender])
                        lanes.append(self._hlanes[conduit.sender])
                    lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
                    if idx != len(self.top_components) - 1:
                        lanes.append(self._hlanes_for_s[conduit.sender])
                    dest = PortPoint(self.top_components[idx], conduit.receiver, True)
                route = ConduitRoute(origin, dest, lanes)
                self._routes.append(route)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        offset = settings.conduit_margin / 2
        height = self._hlanes_for_s.set_y(offset, settings.conduit_margin)
        height += self._hlanes_for_oi.set_y(offset + height, settings.conduit_margin)
        height += self._hlanes.set_y(offset + height, settings.conduit_margin)
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
        self.right_components: dict[Reference, ComponentBlock] = {}
        """Map of components to the right of this duct, keyed by their name."""

        self._destinations: dict[Reference, int] = {}
        """Index in the self.right_components, per destination component"""

        self.vlanes_out = VLanes()
        """Vertical lanes, carrying conduits from left to top."""
        self.vlanes_transfer = VLanes()
        """Vertical lanes, carrying conduits from left to right."""
        self.vlanes_in = VLanes()
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
        else:
            self.right_components[connector.component.name] = connector

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
        idx = self._destinations[conduit.receiving_component()]
        connector = self.right_connectors[idx]
        if isinstance(connector, ComponentBlock):
            return PortPoint(connector, conduit.receiver, False)
        else:
            raise NotImplementedError()  # TODO

    def get_conduits(self) -> Iterator[tuple[Point, Conduit]]:
        for left_connector in self.left_connectors:
            if isinstance(left_connector, ComponentBlock):
                for port, conduits in left_connector.conduits_per_of_port():
                    origin = PortPoint(left_connector, port, False)
                    for conduit in conduits:
                        yield origin, conduit
            else:
                left_connector.route_conduits()
                # TODO: loop over all conduits coming into this timeline...

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        # Temporary values to test layout
        lane_width = settings.conduit_margin
        if not self.left_connectors or not self.right_components:
            lane_width = settings.port_margin

        offset = self.x + lane_width / 2
        width = self.vlanes_out.set_x(offset, lane_width)
        width += self.vlanes_transfer.set_x(offset + width, lane_width)
        width += self.vlanes_in.set_x(offset + width, lane_width)
        self.width = width

        # Only for debug visualization, our conduits can extend below
        self.height = settings.component_height
