from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import svg
from ymmsl.v0_2 import Conduit, Identifier, Operator, Reference, Timeline

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.component_block import ComponentBlock
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


@dataclass
class CruiseLane:
    """Horizontal lane in a TopConduitDuct, at a height that keeps clear of the
    components in its row.

    Unlike a Lane, its y is only resolved when drawing (see
    TopConduitDuct.resolve_cruise), and it is skipped when the route is already at a
    safe height.
    """

    duct: "TopConduitDuct"
    bus: Reference | None = None
    """Key (usually the sender) of the shared bus this lane belongs to: all
    CruiseLanes of one bus resolve to the same y."""
    clamp_to_dest: bool = False
    """Cruise as close as possible to the route's destination y (used for conduits
    leaving this timeline, so they travel in a straight line where possible)."""
    crosses: bool = True
    """Whether the route can pass over one of the row's components. If neither this
    lane nor its bus crosses, the cruise is skipped (see
    TopConduitDuct.cruise_needed)."""


class Lanes:
    """Bundle of horizontal/vertical lanes, indexed by Conduit.sender."""

    def __init__(self, horizontal: bool, reverse: bool = False) -> None:
        """Create a new bundle of lanes

        Args:
            horizontal: Direction of conduits in this lane: True if horizontal, False if
                vertical.
            reverse: By default lanes are drawn top -> bottom (or left -> right) in the
                order they were added. Setting this to True will reverse the order.
        """
        self._horizontal = horizontal
        self._reverse = reverse
        self._lanes: dict[Reference, Lane] = {}

    def set_pos(self, offset: float, spacing: float) -> float:
        """Set the x/y coordinates for all lanes in this bundle."""
        for i, lane in enumerate(self):
            lane.pos = offset + i * spacing
        return len(self) * spacing

    def __iter__(self) -> Iterator[Lane]:
        """Get an iterator over the Lanes in this bundle."""
        if self._reverse:
            return reversed(self._lanes.values())
        return iter(self._lanes.values())

    def __getitem__(self, sender: Reference) -> Lane:
        """Get (and create if required) the Lane for this sender."""
        return self._lanes.setdefault(sender, Lane(self._horizontal))

    def __len__(self) -> int:
        """Get number of lanes in this bundle."""
        return len(self._lanes)

    def keys(self) -> Iterable[Reference]:
        """Get the senders that have a lane in this bundle."""
        return self._lanes.keys()


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


class VirtualPortPoint(Point):
    """Point corresponding to a virtual port of the TopConduitDuct."""

    def __init__(self, tcd: "TopConduitDuct", index: int, left: bool) -> None:
        self.tcd = tcd
        """TopConduitDuct that the virtual port belongs to."""
        self.index = index
        """Index, 0 = top-most virtual port."""
        self.left = left
        """Whether this is a virtual port on the left or on the right side."""

    def __call__(self) -> tuple[float, float]:
        y = self.tcd.vport_y(self.left, self.index)
        if self.left:
            return (0, y)
        return (self.tcd.width, y)


def _translation(tlblock: "TimelineBlock") -> tuple[float, float]:
    """Get the (x, y) offset of a TimelineBlock (see TimelineBlock.moveto)."""
    transform = tlblock.transform
    if not isinstance(transform, svg.Translate):
        return 0, 0
    x, y = transform.x, transform.y
    assert isinstance(x, (float, int))
    assert isinstance(y, (float, int))
    return x, y


class VirtualPortPointInDuct(Point):
    """Point corresponding to a virtual port of a TopConduitDuct, from the
    perspective of the subtimeline's box.
    """

    def __init__(self, tcd: "TopConduitDuct", index: int, left: bool) -> None:
        self.left = left
        """Whether this is a virtual port on the left or on the right of the duct."""
        self.vpp = VirtualPortPoint(tcd, index, not left)

    def __call__(self) -> tuple[float, float]:
        _, y = self.vpp()
        tlblock = self.vpp.tcd.tlblock
        x_offset, y_offset = _translation(tlblock)
        if self.left:
            return (x_offset + tlblock.width, y + y_offset)
        return (x_offset, y + y_offset)


class SiblingVirtualPortPoint(Point):
    """Point for a virtual port on a sibling subtimeline (of the same owning
    component), expressed relative to `frame`'s own coordinate frame -- for a route
    drawn as part of `frame`'s own rendering that needs to reach into a sibling
    instead (see TopConduitDuct._sibling_point_for).
    """

    def __init__(
        self, frame: "TimelineBlock", tcd: "TopConduitDuct", index: int, left: bool
    ) -> None:
        self.frame = frame
        self.point = VirtualPortPointInDuct(tcd, index, left)

    def __call__(self) -> tuple[float, float]:
        x, y = self.point()
        fx, fy = _translation(self.frame)
        return (x - fx, y - fy)


AnyLane = Lane | CruiseLane


def _apply_lanes(
    x: float, y: float, lanes: list[AnyLane], dest_y: float | None = None
) -> tuple[list[svg.PathData], float, float, bool]:
    """Follow `lanes` starting from (x, y).

    Returns the SVG H/V commands, the final (x, y) and whether the last lane used
    was horizontal. `dest_y` is required if `lanes` contains a CruiseLane with
    clamp_to_dest.
    """
    path: list[svg.PathData] = []
    last_horizontal = False
    for lane in lanes:
        if isinstance(lane, CruiseLane):
            new_y = lane.duct.resolve_cruise(lane, y, dest_y)
            if new_y != y:
                last_horizontal = True
                y = new_y
                path.append(svg.V(y))
            continue
        last_horizontal = lane.horizontal
        if lane.horizontal:
            assert lane.pos is not None
            y = lane.pos
            path.append(svg.V(y))
        else:
            assert lane.pos is not None
            x = lane.pos
            path.append(svg.H(x))
    return path, x, y, last_horizontal


class LanePoint(Point):
    """The point reached by starting at `origin` and following `lanes`.

    Used as the end of a shared trunk, which is drawn once, and as the origin of
    every conduit continuing from that trunk. `lanes` must not contain a CruiseLane
    with clamp_to_dest.
    """

    def __init__(self, origin: Point, lanes: list[AnyLane]) -> None:
        self.origin = origin
        self.lanes = lanes

    def __call__(self) -> tuple[float, float]:
        x, y = self.origin()
        _, x, y, _ = _apply_lanes(x, y, self.lanes)
        return (x, y)


@dataclass
class ConduitRoute:
    """Route of a conduit through the conduit ducts in this timeline."""

    origin: Point
    """Origin point in this timeline."""
    destination: Point
    """Destination point in this timeline."""
    lanes: list[AnyLane]
    """Lanes visited (in order) between origin and destination"""
    final_ends_horizontal: bool | None = None
    """Force the final segment into `destination` to be horizontal (True) or
    vertical (False). Use True for F_INIT ports, whose shape only looks right when
    approached horizontally. None infers it from the lanes."""

    def to_svg(self) -> svg.Path:
        """Create an SVG Path to describe this conduit route."""
        x, y = self.origin()
        path: list[svg.PathData] = [svg.M(x, y)]
        dest_x, dest_y = self.destination()
        lane_path, x, y, last_horizontal = _apply_lanes(x, y, self.lanes, dest_y)
        path.extend(lane_path)
        if self.final_ends_horizontal is not None:
            # The final segment is perpendicular to the last lane
            last_horizontal = not self.final_ends_horizontal
        # Skip zero-length moves: with square line caps they would show as a stub
        h_v = [(dest_x != x, svg.H(dest_x)), (dest_y != y, svg.V(dest_y))]
        if not last_horizontal:
            h_v.reverse()
        for moves, command in h_v:
            if moves:
                path.append(command)
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

        self._routes: list[ConduitRoute] = []
        """List of conduit routes through this timeline."""
        self._entry_points: dict[Reference, Point] = {}
        """Per sender: end of the entry lanes shared by all its conduits (see
        route_conduits), so they are drawn only once."""
        self._trunk_points: dict[Reference, Point] = {}
        """Per sender: current end of its shared bus (see _join_trunk)."""
        self._cruise_heights_used: list[float] = []
        """Heights already used by CruiseLanes in this row, which later cruises keep
        clear of (see safe_cruise_height)."""
        self._vport_y: dict[tuple[bool, int], float] = {}
        """Explicit y of (left, index) virtual ports (see add_virtual_port)."""
        self._bus_heights: dict[Reference, float] = {}
        """Resolved height of each shared bus (see CruiseLane.bus)."""
        self._crossing_buses: set[Reference] = set()
        """Shared buses with a branch that passes over one of this row's
        components (see cruise_needed)."""

    def _cruise(
        self,
        bus: Reference | None = None,
        clamp_to_dest: bool = False,
        crosses: bool = True,
    ) -> CruiseLane:
        """Create a CruiseLane in this row, registering its shared bus as crossing if
        this branch crosses."""
        if crosses and bus is not None:
            self._crossing_buses.add(bus)
        return CruiseLane(self, bus, clamp_to_dest, crosses)

    def cruise_needed(self, lane: CruiseLane) -> bool:
        """Whether `lane` can pass over one of this row's components, either itself
        or through another branch of its shared bus."""
        return lane.crosses or lane.bus in self._crossing_buses

    def resolve_cruise(self, lane: CruiseLane, y: float, dest_y: float | None) -> float:
        """Get the y that a route currently at `y` cruises at for `lane`.

        Returns `y` itself if no cruise is needed: either nothing in this row has to
        be cleared, or `y` is already a safe height. y == 0 is never safe, as that
        is where the O_I ports of this row's top components sit. Otherwise the
        result is a safe_cruise_height. Every resolved height is recorded so later
        cruises keep clear of it, and a shared bus resolves only once.
        """
        if not self.cruise_needed(lane):
            return y
        if lane.bus is not None and lane.bus in self._bus_heights:
            return self._bus_heights[lane.bus]
        if 0 < y <= self.height:
            new_y = y
        elif lane.clamp_to_dest:
            assert dest_y is not None
            new_y = self.safe_cruise_height(below=dest_y)
        else:
            new_y = self.safe_cruise_height()
        if lane.bus is not None:
            self._bus_heights[lane.bus] = new_y
        self._cruise_heights_used.append(new_y)
        return new_y

    def vport_y(self, left: bool, index: int) -> float:
        """Get the y-coordinate of a left/right virtual port."""
        override = self._vport_y.get((left, index))
        if override is not None:
            return override
        # Left and right ports are numbered independently, so offset the right ones
        # by half a vport_margin to interleave them with the left ones: otherwise an
        # entering and an exiting conduit could line up and look like a single one.
        offset = 0.5 if left else 1.0
        return (index + offset) * settings.vport_margin

    def add_conduit_duct(self, conduit_duct: "ConduitDuct") -> None:
        """Register conduit duct."""
        self.ducts.append(conduit_duct)

    def add_top_component(self, component: ComponentBlock) -> None:
        """Register parent component connecting to the top"""
        self.top_components.append(component)

    def add_virtual_port(
        self,
        conduit: Conduit,
        left: bool,
        key: Reference | None = None,
        y: float | None = None,
    ) -> int:
        """Add a new conduit to a virtual port and return its index.

        Args:
            conduit: Conduit to add.
            left: Choose between the left / right virtual ports.
            key: Group conduits under this reference instead of conduit.sender, e.g.
                to merge different senders heading to the same destination.
            y: Place this port at this y instead of the index-based one, so a
                conduit passing through can keep going straight. Ignored if another
                port is already placed at this y.
        """
        if key is None:
            key = conduit.sender
        vports = self.left_vports if left else self.right_vports
        vports.setdefault(key, []).append(conduit)
        idx = list(vports).index(key)
        if y is not None and y not in self._vport_y.values():
            self._vport_y[(left, idx)] = y
        return idx

    def port_offsets(self, component: ComponentBlock) -> tuple[float, float]:
        """Get offsets for the left-most O_I and right-most S port of a component.

        The ports line up with ducts[0].vlanes_in (O_I) and ducts[-1].vlanes_out
        (S), so conduits go straight up into them. vlanes_out comes first in
        ducts[-1], vlanes_in comes last in ducts[0] (see ConduitDuct.calc_layout).
        """
        oi_offset = s_offset = 0
        if component == self.top_components[0]:
            oi_offset = max(
                len(self.left_vports) * settings.port_margin,
                self.ducts[0].width_before_vlanes_in(),
            )
        if component == self.top_components[-1]:
            s_offset = -self.ducts[-1].width
        return oi_offset, s_offset

    def vlanes_in_is_own(self) -> bool:
        """Whether ducts[0].vlanes_in only carries conduits from the O_I ports of
        top_components[0], and not e.g. conduits hopping in from a sibling.

        Only then can vlanes_in be shifted as a whole without misaligning anything
        (see TimelineBlock._boundary_duct_padding).
        """
        if not self.top_components:
            return False
        own_ports = set(
            self.top_components[0].ports_per_operator(Operator.O_I, self.timeline)
        )
        return set(self.ducts[0].vlanes_in.keys()) <= own_ports

    def own_s_port_count(self) -> int:
        """Number of S ports top_components[-1] has for this timeline."""
        if not self.top_components:
            return 0
        return len(
            list(self.top_components[-1].ports_per_operator(Operator.S, self.timeline))
        )

    def safe_cruise_height(self, below: float | None = None) -> float:
        """Get a y in this row that is at least hlane_margin away from the
        components below and from all heights already used by other cruises.

        Without `below`, return the topmost such y: the first cruise (going furthest
        right) is highest, so later ones pass underneath without crossing it. With
        `below`, return the lowest such y at or above `below`.
        """
        margin = settings.hlane_margin
        bottom = self.height - margin
        if below is None:
            candidate = margin
            for used in sorted(self._cruise_heights_used):
                if abs(candidate - used) < margin:
                    candidate = used + margin
            return min(candidate, bottom)
        candidate = min(below, bottom)
        for used in sorted(self._cruise_heights_used, reverse=True):
            if abs(candidate - used) < margin:
                candidate = used - margin
        return candidate

    def _join_trunk(self, sender: Reference, origin: Point) -> Point:
        """Get the point where `sender`'s shared bus currently ends, creating it
        (starting from `origin`) the first time.

        Each conduit of the sender continues from where the previous one left the
        bus (see route_conduits), so a broadcast is drawn as one line with a branch
        per destination.
        """
        trunk_point = self._trunk_points.get(sender)
        if trunk_point is None:
            # Whether the trunk is needed depends on its branches (see cruise_needed)
            trunk_lanes: list[AnyLane] = [self._cruise(bus=sender, crosses=False)]
            trunk_point = LanePoint(origin, trunk_lanes)
            self._trunk_points[sender] = trunk_point
            self._routes.append(ConduitRoute(origin, trunk_point, trunk_lanes))
        return trunk_point

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

    def _sibling_point_for(self, conduit: Conduit) -> Point | None:
        """Hand `conduit`, which leaves this timeline, to the next sibling subtimeline
        (of the same top component) on the right.

        Conduits pass through every sibling to the right on their way out. Returns
        the Point (relative to this timeline) of the virtual port on that sibling,
        or None if there is no sibling to our right.
        """
        if not self.top_components:
            return None
        siblings = self.top_components[0].subtimelines
        idx = siblings.index(self.tlblock)
        if idx == len(siblings) - 1:
            return None
        tcd = siblings[idx + 1].top_conduit_duct
        vidx = tcd.add_virtual_port(conduit, left=True)
        return SiblingVirtualPortPoint(self.tlblock, tcd, vidx, left=False)

    def _route_to_sibling_or_parent(
        self,
        conduit: Conduit,
        preferred_y: float | None = None,
        lane_key: Reference | None = None,
    ) -> tuple[list[Lane], Point]:
        """Route a conduit with no destination in this timeline to the next sibling
        subtimeline (see _sibling_point_for), or else to our parent timeline. In the
        latter case conduits are grouped by receiver, so conduits from different
        senders heading to the same destination share a lane.

        Args:
            conduit: Conduit to route.
            preferred_y: y of the exit port, if any (see add_virtual_port).
            lane_key: Key of the exit lane in ducts[-1], instead of
                conduit.receiver (see _shares_sending_port).
        """
        sibling = self._sibling_point_for(conduit)
        if sibling is not None:
            return [], sibling
        lanes = [self.ducts[-1].vlanes_out[lane_key or conduit.receiver]]
        idx = self.add_virtual_port(
            conduit, left=False, key=conduit.receiver, y=preferred_y
        )
        return lanes, VirtualPortPoint(self, idx, left=False)

    def _shares_sending_port(self, conduit: Conduit) -> bool:
        """Whether `conduit` comes straight from one of this row's components, from a
        port that also sends other conduits. Such a conduit exits on its sender's lane
        (shared with those other conduits, e.g. one going up to an S port of the top
        component) instead of on a lane grouped by receiver."""
        for component in self.tlblock.components:
            if component.component.name == conduit.sending_component():
                port_conduits = component.conduits_per_port[conduit.sending_port()]
                return len(port_conduits) > 1
        return False

    def _get_input_conduits(self) -> Iterator[tuple[int, Point, Conduit, bool]]:
        """Iterator over all input conduits.

        This iterator yields all conduits coming from:
        - Top components (component_index >= 0)
        - Parent timeline (component_index == -1)

        Yields:
            (component_index, origin_point, conduit, shares_sender) for each conduit,
            starting with conduits connected to the right-most port. shares_sender is
            True if conduit.sender has more than one outgoing conduit here.
        """
        # Conduits coming from top components
        idx = len(self.top_components)
        for component in reversed(self.top_components):
            idx -= 1
            for conduit in component.conduits_per_operator(
                Operator.O_I, self.timeline, reverse=True
            ):
                origin = PortPoint(component, conduit.sending_port())
                port_conduits = component.conduits_per_port[conduit.sending_port()]
                yield (idx, origin, conduit, len(port_conduits) > 1)
        # Conduits coming from the parent timeline
        if self.left_conduit_duct is None:
            # Reserve vlanes of model ports so that the top-most port gets the
            # right-most lane (vlanes_in is numbered in reverse), to avoid crossings.
            for name in self.left_vports:
                self.ducts[0].vlanes_in[name]
        # Then assign conduits in regular order
        for idx, conduits in enumerate(self.left_vports.values()):
            origin = VirtualPortPoint(self, idx, left=True)
            for conduit in conduits:
                yield (-1, origin, conduit, len(conduits) > 1)

    def _get_duct_conduits(self) -> Iterator[tuple[int, Reference, Point, Conduit]]:
        """Iterator over all conduits connected to ConduitDucts.

        Yields:
            (duct_index, key, origin_point, conduit) for each conduit, starting with
            conduits connected to the right-most port. key is the reference lanes
            local to this timeline should be grouped by for this conduit -- see
            ConduitDuct.get_conduits.
        """
        for idx, duct in enumerate(self.ducts):
            for key, origin, conduit in duct.get_conduits():
                yield (idx, key, origin, conduit)

    def _finish_bottom_route(
        self, route_origin: Point, idest: int, lanes: list[AnyLane], conduit: Conduit
    ) -> None:
        """Add a route to a destination in ducts[idest], approaching F_INIT ports
        horizontally."""
        dest = self.ducts[idest].get_point_for(conduit)
        final_ends_horizontal = True if isinstance(dest, PortPoint) else None
        self._routes.append(
            ConduitRoute(route_origin, dest, lanes, final_ends_horizontal)
        )

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
            # Reserve space for all S ports in the last component
            local_senders = {c.component.name for c in self.tlblock.components}
            for port in self.top_components[-1].ports_per_operator(
                Operator.S, self.timeline
            ):
                port_id = port[-1]
                assert isinstance(port_id, Identifier)
                conduits = self.top_components[-1].conduits_per_port[port_id]
                if conduits:
                    assert len(conduits) == 1  # S port can have at most 1 conduit
                    conduit = conduits[0]
                    # Use the same key as routing will: the sender for conduits
                    # from this row, the receiver for conduits coming out of a
                    # subtimeline (see _route_to_sibling_or_parent).
                    if conduit.sending_component() in local_senders:
                        key = conduit.sender
                    else:
                        key = conduit.receiver
                    self.ducts[-1].vlanes_out[key]
                else:
                    # No conduits connected to this port, but still reserve space:
                    self.ducts[-1].vlanes_out[port]

        # Route conduits coming from top components and parent timelines
        for itop, origin, conduit, shares_sender in self._get_input_conduits():
            destination = self._destinations.get(conduit.receiving_component())
            lanes: list[AnyLane] = []
            if itop > 0:
                lanes.append(self._hlanes_for_oi[conduit.sender])
            # Always reserve the vlanes_in lane, so the position of other lanes does
            # not depend on whether this conduit uses it. A single conduit passing
            # through this timeline doesn't need it.
            vlane_in_0 = self.ducts[0].vlanes_in[conduit.sender]
            if shares_sender or destination is not None:
                lanes.append(vlane_in_0)
            route_origin = origin
            if shares_sender:
                # All conduits of this sender share the lanes so far: draw them once
                entry_point = self._entry_points.get(conduit.sender)
                if entry_point is None:
                    entry_point = LanePoint(origin, lanes)
                    self._entry_points[conduit.sender] = entry_point
                    self._routes.append(ConduitRoute(origin, entry_point, lanes))
                route_origin = entry_point
                lanes = []
            if destination is None:  # Route to a sibling subtimeline, or the parent
                preferred_y = None
                if shares_sender:
                    route_origin = self._join_trunk(conduit.sender, route_origin)
                    # The hop travels at the trunk's height, across the whole row
                    self._crossing_buses.add(conduit.sender)
                else:
                    # Cruise close to the y of the exit, to go straight if possible
                    lanes.append(self._cruise(clamp_to_dest=True))
                    if isinstance(origin, VirtualPortPoint):
                        # Passing straight through this timeline: exit at the same y
                        preferred_y = origin()[1]
                hop_lanes, dest = self._route_to_sibling_or_parent(conduit, preferred_y)
                lanes.extend(hop_lanes)

            elif destination[0] == "B":  # Route to a Bottom destination
                idest = destination[1]
                if shares_sender:
                    route_origin = self._join_trunk(conduit.sender, route_origin)
                # Only a destination past ducts[0] passes over one of our components
                lanes.append(self._cruise(bus=conduit.sender, crosses=idest > 0))
                if idest > 0:
                    extend_lane = self.ducts[idest].vlanes_in[conduit.sender]
                    lanes.append(extend_lane)
                    if shares_sender:
                        # The next conduit of this sender continues from here
                        self._trunk_points[conduit.sender] = LanePoint(
                            route_origin, [extend_lane]
                        )
                self._finish_bottom_route(route_origin, idest, lanes, conduit)
                continue

            elif destination[0] == "T":  # Route to a Top destination
                continue  # TODO, interact coupling

            route = ConduitRoute(route_origin, dest, lanes)
            self._routes.append(route)

        # Route conduits coming from internal components and subtimelines. Lanes are
        # grouped by `key` (see ConduitDuct.get_conduits).
        for iduct, key, origin, conduit in self._get_duct_conduits():
            destination = self._destinations.get(conduit.receiving_component())
            lanes: list[AnyLane] = []
            if destination is None:  # Route to a sibling subtimeline, or the parent
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[key])
                    lanes.append(self._cruise(clamp_to_dest=True))
                lane_key = key if self._shares_sending_port(conduit) else None
                hop_lanes, dest = self._route_to_sibling_or_parent(
                    conduit, lane_key=lane_key
                )
                lanes.extend(hop_lanes)

            elif destination[0] == "B":  # Route to a Bottom destination
                idest = destination[1]
                if iduct == idest:
                    # Already inside the destination duct: cross it directly instead
                    # of first exiting and re-entering.
                    lanes.append(self.ducts[iduct].vlanes_transfer[key])
                else:
                    lanes.append(self.ducts[iduct].vlanes_out[key])
                    lanes.append(self._cruise(bus=key))
                    lanes.append(self.ducts[idest].vlanes_in[key])
                self._finish_bottom_route(origin, idest, lanes, conduit)
                continue

            else:  # Route to a Top destination
                idest = destination[1]
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[key])
                    lanes.append(self._cruise(bus=key))
                lanes.append(self.ducts[-1].vlanes_out[key])
                if idest != len(self.top_components) - 1:
                    lanes.append(self._hlanes_for_s[key])
                dest = PortPoint(self.top_components[idest], conduit.receiving_port())

            route = ConduitRoute(origin, dest, lanes)
            self._routes.append(route)

    def calc_layout(self) -> None:
        """Calculate size and layout of this component"""
        offset = settings.hlane_margin / 2
        height = self._hlanes_for_s.set_pos(offset, settings.hlane_margin)
        height += self._hlanes_for_oi.set_pos(offset + height, settings.hlane_margin)
        if height:
            height += settings.hlane_margin
        # Reserve height for every cruise (see resolve_cruise), as each one keeps
        # clear of the previous ones. This is worst-case: we don't know yet which ones
        # will be skipped. N cruises need (N+1) margins, as a skipped cruise may be
        # at any height in this row. A shared bus counts once.
        needed = [
            lane
            for route in self._routes
            for lane in route.lanes
            if isinstance(lane, CruiseLane) and self.cruise_needed(lane)
        ]
        buses = {lane.bus for lane in needed if lane.bus is not None}
        cruising_lanes = len(buses) + sum(1 for lane in needed if lane.bus is None)
        if cruising_lanes:
            height += (cruising_lanes + 1) * settings.hlane_margin
        # Make all virtual ports safe heights to cruise at, so routes crossing this
        # row towards a virtual port can go straight.
        if cruising_lanes and (self.left_vports or self.right_vports):
            port_extent = max(
                (len(self.left_vports) - 0.5) * settings.vport_margin
                if self.left_vports
                else 0,
                len(self.right_vports) * settings.vport_margin
                if self.right_vports
                else 0,
            )
            height = max(height, port_extent + settings.hlane_margin)
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
        self.vlanes_in = Lanes(horizontal=False, reverse=True)
        """Vertical lanes, carrying conduits from top to right."""
        self._leading_pad: float = 0.0
        """Extra space before vlanes_in (see calc_layout)."""

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
            return PortPoint(connector, conduit.receiving_port())
        # Always enter via the first (duct-adjacent) subtimeline. If the destination
        # lives in a later sibling, that subtimeline's own routing hands the conduit
        # on (see TopConduitDuct._sibling_point_for).
        connector = connector.top_components[0].subtimelines[0].top_conduit_duct
        vidx = connector.add_virtual_port(conduit, left=True)
        return VirtualPortPointInDuct(connector, vidx, left=False)

    def get_conduits(self) -> Iterator[tuple[Reference, Point, Conduit]]:
        """Iterator over all conduits that enter this conduit duct from left_connectors.

        Yields:
            (key, origin_point, conduit) for each conduit. key is the reference that
            lanes in the receiving timeline are grouped by: conduit.sender for a
            conduit from a component, or the virtual port key for a conduit coming
            from a subtimeline (see TopConduitDuct.add_virtual_port).
        """
        for left_connector in self.left_connectors:
            if isinstance(left_connector, ComponentBlock):
                for conduit in left_connector.conduits_per_operator(Operator.O_F):
                    origin = PortPoint(left_connector, conduit.sending_port())
                    yield conduit.sender, origin, conduit
            else:
                left_connector.route_conduits()
                # Loop over conduits coming in from the child timeline
                for idx, (key, conduits) in enumerate(
                    left_connector.right_vports.items()
                ):
                    origin = VirtualPortPointInDuct(left_connector, idx, left=True)
                    for conduit in conduits:
                        yield key, origin, conduit

    def lane_width(self) -> float:
        """Spacing between vlanes: port_margin at the edges of a timeline (to line up
        with the O_I/S ports above), conduit_margin between two components."""
        if not self.left_connectors or not self.right_connectors:
            return settings.port_margin
        return settings.conduit_margin

    def width_before_vlanes_in(self) -> float:
        """Width of everything before vlanes_in (see calc_layout)."""
        lanes_width = (
            len(self.vlanes_out) + len(self.vlanes_transfer)
        ) * self.lane_width()
        return lanes_width + self._leading_pad

    def calc_layout(self, leading_pad: float = 0.0) -> None:
        """Calculate size and layout of this component.

        Args:
            leading_pad: Extra space to insert before vlanes_in, which moves the O_I
                ports aligned with it (see TimelineBlock._boundary_duct_padding).
        """
        self._leading_pad = leading_pad
        lane_width = self.lane_width()
        offset = self.x + lane_width / 2
        width = self.vlanes_out.set_pos(offset, lane_width)
        width += self.vlanes_transfer.set_pos(offset + width, lane_width)
        width += leading_pad
        width += self.vlanes_in.set_pos(offset + width, lane_width)
        self.width = width

        # Only for debug visualization, our conduits can extend below
        self.height = settings.component_height
