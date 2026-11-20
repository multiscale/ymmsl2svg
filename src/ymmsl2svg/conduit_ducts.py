from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import svg
from ymmsl.v0_2 import Conduit, Operator, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
from ymmsl2svg.port_groups import HorizontalPortGroup, PortBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.timeline_block import TimelineBlock


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


@dataclass
class VirtualPortPoint:
    """Point corresponding to a virtual port of a TopConduitDuct."""

    tcd: "TopConduitDuct"
    """TopConduitDuct that the virtual port belongs to."""
    index: int
    """Index, 0 = top-most virtual port."""
    left: bool
    """Whether this is a virtual port on the left or on the right side."""

    @property
    def position(self) -> tuple[float, float]:
        """(x, y) coordinate of this point."""
        return (0 if self.left else self.tcd.width, self.tcd.virtual_port_y(self.index))


def _translation(tlblock: "TimelineBlock") -> tuple[float, float]:
    """Get the (x, y) offset of a TimelineBlock (see TimelineBlock.moveto)."""
    transform = tlblock.transform
    if not isinstance(transform, svg.Translate):
        return 0, 0
    x, y = transform.x, transform.y
    assert isinstance(x, (float, int))
    assert isinstance(y, (float, int))
    return x, y


@dataclass
class VirtualPortPointInDuct:
    """Point corresponding to a virtual port of a TopConduitDuct, from the
    perspective of the timeline containing that subtimeline.

    The point is on the edge of the subtimeline: with several subtimelines side by
    side, that edge need not touch the ConduitDuct.
    """

    tcd: "TopConduitDuct"
    """TopConduitDuct that the virtual port belongs to."""
    index: int
    """Index, 0 = top-most virtual port."""
    left: bool
    """Whether this is a virtual port on the left or on the right of the duct."""

    @property
    def position(self) -> tuple[float, float]:
        """(x, y) coordinate of this point."""
        tlblock = self.tcd.tlblock
        x_offset, y_offset = _translation(tlblock)
        y = self.tcd.virtual_port_y(self.index) + y_offset
        if self.left:
            return (x_offset + tlblock.width, y)
        return (x_offset, y)


@dataclass
class SiblingVirtualPortPoint:
    """Point for a virtual port on a sibling subtimeline (of the same parent
    component), relative to the frame of `frame` (see
    TopConduitDuct._sibling_point_for).
    """

    frame: "TimelineBlock"
    """Timeline whose coordinate frame the position is relative to."""
    point: VirtualPortPointInDuct
    """Virtual port on the sibling subtimeline."""

    @property
    def position(self) -> tuple[float, float]:
        """(x, y) coordinate of this point."""
        x, y = self.point.position
        fx, fy = _translation(self.frame)
        return (x - fx, y - fy)


Point = VirtualPortPoint | VirtualPortPointInDuct | SiblingVirtualPortPoint | PortBlock
"""Start / end point of a ConduitRoute."""


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
        x, y = self.origin.position
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
        x, y = self.destination.position
        if self.lanes and self.lanes[-1].horizontal:
            path.append(svg.H(x))
            path.append(svg.V(y))
        else:
            path.append(svg.V(y))
            path.append(svg.H(x))
        return svg.Path(d=path, class_=["conduit"])


class TopConduitDuct(SvgBlock):
    """Top conduit duct in a timeline."""

    def __init__(self, tlblock: "TimelineBlock", timeline: Timeline) -> None:
        super().__init__()
        self.tlblock = tlblock
        self.timeline = timeline

        self.left_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the left."""
        self.right_conduit_duct: ConduitDuct | None = None  # Set by ConduitDuct
        """Optional conduit duct (from a parent timeline) connecting to the right."""
        self.left_vports: dict[Reference, list[Conduit]] = {}  # Filled by ConduitDuct
        """Virtual ports for conduits arriving from self.left_conduit_duct."""
        self.right_vports: dict[Reference, list[Conduit]] = {}
        """Virtual ports for conduits going to self.right_conduit_duct."""

        self.top_port_groups: list[HorizontalPortGroup] = []
        """HorizontalPortBlocks of the parent components connecting to the top."""
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

    def add_port_group(self, port_group: HorizontalPortGroup) -> None:
        """Register the ports of a parent component connecting to the top"""
        self.top_port_groups.append(port_group)

    @staticmethod
    def virtual_port_y(index: int) -> float:
        """y-coordinate of the virtual port with this index, in this duct's frame."""
        # N.B. using port_margin here to align with F_INIT / O_F Model ports
        return (index + 0.5) * settings.port_margin

    def add_virtual_port(self, conduit: Conduit, left: bool) -> int:
        """Add a new conduit to a virtual port and return its index.

        Args:
            conduit: Conduit to add.
            left: Choose between the left / right virtual ports.
        """
        vports = self.left_vports if left else self.right_vports
        if conduit.sender in vports:
            vports[conduit.sender].append(conduit)
            for idx, sender in enumerate(vports):
                if sender == conduit.sender:
                    return idx
        idx = len(vports)
        vports[conduit.sender] = [conduit]
        return idx

    def port_offsets(self, port_group: HorizontalPortGroup) -> tuple[float, float]:
        """Get offsets for the left-most O_I and right-most S port of a port group."""
        oi_offset = s_offset = 0
        if port_group == self.top_port_groups[0]:
            oi_offset = len(self.left_vports) * settings.port_margin
        if port_group == self.top_port_groups[-1]:
            s_offset = -len(self.ducts[-1].vlanes_out) * settings.port_margin
        return oi_offset, s_offset

    def _fill_destinations(self) -> None:
        """Build lookup map for component destinations."""
        for i, port_group in enumerate(self.top_port_groups):
            self._destinations[port_group.name] = ("T", i)
        for i, duct in enumerate(self.ducts):
            dest = ("B", i)
            for destination in duct.destinations():
                self._destinations[destination] = dest

    def destinations(self) -> Iterable[Reference]:
        """Get components that are inside this timeline or a subtimeline."""
        if not self._destinations:
            self._fill_destinations()
        return self._destinations.keys()

    def _sibling_point_for(self, conduit: Conduit) -> Point | None:
        """Hand `conduit`, which leaves this timeline, to the next sibling subtimeline
        (of the same parent component) on the right.

        Conduits pass through every sibling to the right on their way out. Returns
        the Point (relative to this timeline) of the virtual port on that sibling,
        or None if there is no sibling to our right.
        """
        if self.tlblock.parent is None:
            return None
        siblings = self.tlblock.parent.subtimelines
        idx = siblings.index(self.tlblock)
        if idx == len(siblings) - 1:
            return None
        tcd = siblings[idx + 1].top_conduit_duct
        vidx = tcd.add_virtual_port(conduit, left=True)
        point = VirtualPortPointInDuct(tcd, vidx, left=False)
        return SiblingVirtualPortPoint(self.tlblock, point)

    def _exit_point_for(self, conduit: Conduit) -> Point:
        """Get the Point where `conduit`, which has no destination in this timeline,
        leaves it: the next sibling subtimeline if there is one, else our parent's
        timeline (through a right virtual port)."""
        sibling = self._sibling_point_for(conduit)
        if sibling is not None:
            return sibling
        idx = self.add_virtual_port(conduit, left=False)
        return VirtualPortPoint(self, idx, left=False)

    def _get_input_conduits(self) -> Iterator[tuple[int, Point, Conduit]]:
        """Iterator over all input conduits.

        This iterator yields all conduits coming from:
        - Top components (component_index >= 0)
        - Parent timeline (component_index == -1)

        Yields:
            (component_index, origin_point, conduit) for each conduit, starting with
            conduits connected to the right-most port.
        """
        # Conduits coming from top components
        idx = len(self.top_port_groups)
        for port_group in reversed(self.top_port_groups):
            idx -= 1
            for port in reversed(port_group.ports(Operator.O_I)):
                for conduit in port.conduits:
                    yield (idx, port, conduit)
        # Conduits coming from the parent timeline
        if self.left_conduit_duct is None:
            # Reserve vlanes of model ports in reverse order
            for name in reversed(self.left_vports):
                self.ducts[0].vlanes_in[name]
        # Then assign conduits in regular order
        for idx, conduits in enumerate(self.left_vports.values()):
            origin = VirtualPortPoint(self, idx, left=True)
            for conduit in conduits:
                yield (-1, origin, conduit)

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

        if self.top_port_groups:
            # Reserve space for all O_I ports in the first component
            for port in reversed(self.top_port_groups[0].ports(Operator.O_I)):
                self.ducts[0].vlanes_in[port.ref]
            # Reserve space for all S ports in the last component
            for port in self.top_port_groups[-1].ports(Operator.S):
                if port.conduits:
                    assert len(port.conduits) == 1  # S port has at most 1 conduit
                    self.ducts[-1].vlanes_out[port.conduits[0].sender]
                else:
                    # No conduits connected to this port, but still reserve space:
                    self.ducts[-1].vlanes_out[port.ref]

        # Route conduits coming from top components and parent timelines
        for itop, origin, conduit in self._get_input_conduits():
            lanes = []
            if itop > 0:
                lanes.append(self._hlanes_for_oi[conduit.sender])
            destination = self._destinations.get(conduit.receiving_component())
            if destination is None:  # Route to a sibling subtimeline, or the parent
                lanes.append(self.ducts[0].vlanes_in[conduit.sender])
                lanes.append(self._hlanes[conduit.sender])
                lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
                dest = self._exit_point_for(conduit)

            elif destination[0] == "B":  # Route to a Bottom destination
                lanes.append(self.ducts[0].vlanes_in[conduit.sender])
                idest = destination[1]
                if idest > 0:
                    lanes.append(self._hlanes[conduit.sender])
                    lanes.append(self.ducts[idest].vlanes_in[conduit.sender])
                dest = self.ducts[idest].get_point_for(conduit)

            elif destination[0] == "T":  # Route to a Top destination
                continue  # TODO, interact coupling

            route = ConduitRoute(origin, dest, lanes)
            self._routes.append(route)

        # Route conduits coming from internal components and subtimelines
        for iduct, origin, conduit in self._get_duct_conduits():
            destination = self._destinations.get(conduit.receiving_component())
            lanes = []
            if destination is None:  # Route to a sibling subtimeline, or the parent
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[conduit.sender])
                    lanes.append(self._hlanes[conduit.sender])
                lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
                dest = self._exit_point_for(conduit)

            elif destination[0] == "B":  # Route to a Bottom destination
                idest = destination[1]
                if iduct == idest:
                    lanes.append(self.ducts[iduct].vlanes_transfer[conduit.sender])
                else:
                    lanes.append(self.ducts[iduct].vlanes_out[conduit.sender])
                    lanes.append(self._hlanes[conduit.sender])
                    lanes.append(self.ducts[idest].vlanes_in[conduit.sender])
                dest = self.ducts[idest].get_point_for(conduit)

            else:  # Route to a Top destination
                idest = destination[1]
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[conduit.sender])
                    lanes.append(self._hlanes[conduit.sender])
                lanes.append(self.ducts[-1].vlanes_out[conduit.sender])
                if idest != len(self.top_port_groups) - 1:
                    lanes.append(self._hlanes_for_s[conduit.sender])
                component = self.top_port_groups[idest].component
                dest = component.ports[conduit.receiving_port()]

            route = ConduitRoute(origin, dest, lanes)
            self._routes.append(route)

    def calc_layout(self) -> None:
        """Calculate layout of this component.

        N.B. The height is set by the TimelineBlock, from the minimum_height of all top
        conduit ducts in its timeline layer.
        """
        offset = settings.conduit_margin / 2
        offset += self._hlanes_for_s.set_pos(offset, settings.conduit_margin)
        offset += self._hlanes_for_oi.set_pos(offset, settings.conduit_margin)
        self._hlanes.set_pos(offset, settings.conduit_margin)

    @property
    def minimum_height(self) -> float:
        """The minimum height needed to draw all conduits inside this duct."""
        num_lanes = (
            len(self._hlanes_for_s) + len(self._hlanes_for_oi) + len(self._hlanes)
        )
        if not num_lanes:
            return 0
        return (num_lanes + 1) * settings.conduit_margin

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

    def _fill_destinations(self) -> None:
        """Build lookup map for component destinations."""
        for i, connector in enumerate(self.right_connectors):
            if isinstance(connector, ComponentBlock):
                self._destinations[connector.component.name] = i
            else:
                for destination in connector.destinations():
                    self._destinations.setdefault(destination, i)

    def destinations(self) -> Iterable[Reference]:
        """Return all components reachable through this duct."""
        if not self._destinations:
            self._fill_destinations()
        return self._destinations.keys()

    def get_point_for(self, conduit: Conduit) -> Point:
        """Get a Point to describe the position of the destination of the conduit."""
        idx = self._destinations[conduit.receiving_component()]
        connector = self.right_connectors[idx]
        if isinstance(connector, ComponentBlock):
            return connector.ports[conduit.receiving_port()]
        # Always enter via the first (duct-adjacent) subtimeline. If the destination
        # is in a later sibling, that subtimeline hands the conduit on (see
        # TopConduitDuct._sibling_point_for).
        assert connector.tlblock.parent is not None
        connector = connector.tlblock.parent.subtimelines[0].top_conduit_duct
        idx = connector.add_virtual_port(conduit, left=True)
        return VirtualPortPointInDuct(connector, idx, left=False)

    def get_conduits(self) -> Iterator[tuple[Point, Conduit]]:
        """Iterator over all conduits that enter this conduit duct from left_connectors.

        Yields:
            (origin_point, conduit) for each conduit.
        """
        for left_connector in self.left_connectors:
            if isinstance(left_connector, ComponentBlock):
                for port in left_connector.o_f_group.ports(Operator.O_F):
                    for conduit in port.conduits:
                        yield port, conduit
            else:
                left_connector.route_conduits()
                # Loop over conduits coming in from the child timeline:
                for idx, conduits in enumerate(left_connector.right_vports.values()):
                    origin = VirtualPortPointInDuct(left_connector, idx, left=True)
                    for conduit in conduits:
                        yield origin, conduit

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
