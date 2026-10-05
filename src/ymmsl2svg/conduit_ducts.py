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
    """A horizontal cruise lane (see Lane) that a route only actually needs to visit
    if it isn't already at a y that stays clear of every component in `duct`'s own
    row (see TopConduitDuct.safe_cruise_height) -- resolved dynamically at render
    time (see _apply_lanes), once layout has fixed `duct.height` and every other
    route's own cruise, rather than through a statically pre-assigned position the
    way a plain Lane's is.

    Two routing situations need this:

    - A conduit hopping past its own row entirely (to a sibling subtimeline, or
      genuinely exiting to the parent): cruises at its own destination y, clamped
      down to whatever is actually safe (`clamp_to_dest=True`) -- so it travels in
      one straight line whenever the destination is already safe, with only a
      small final correction otherwise.
    - A shared per-sender bus (e.g. a "repeat" broadcast, see
      TopConduitDuct._join_trunk): every branch needs to agree on the exact same
      y, so `shared_lane` (identifying the bus by its underlying, otherwise-unused
      Lane) caches the first resolution and hands it to every later branch instead
      of letting each one compute its own answer against a `_cruise_heights_used`
      that may have moved on by the time it gets there.
    """

    duct: "TopConduitDuct"
    shared_lane: Lane | None = None
    """Set for a shared bus (see above): every CruiseLane wrapping the same Lane
    resolves to one cached height. None for a plain per-route cruise that nothing
    else needs to agree with."""
    clamp_to_dest: bool = False
    """Clamp the resolved height to the route's own destination y (see above)."""
    crosses: bool = True
    """Whether the horizontal travel after this cruise can pass over one of the row's
    components. If not (and its shared bus doesn't cross either, see
    TopConduitDuct.cruise_needed), the cruise is skipped entirely and reserves no
    height in the row."""


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
        override = self.tcd._vport_y.get((self.left, self.index))
        if override is not None:
            y = override
        else:
            # left and right ports are numbered independently (0, 1, 2, ... on
            # each side), so without the extra +0.5 below, the first port
            # entering on the left and the first port exiting on the right would
            # land on the exact same y -- looking, across the component in
            # between that hides the gap, like one conduit passing straight
            # through the other's box, rather than the two unrelated conduits
            # they actually are. Offsetting the right side by half a vport_margin
            # makes the two sides' y's interleave instead, however many ports
            # either one has, without needing to know the other side's count.
            index = self.index if self.left else self.index + 0.5
            y = (index + 0.5) * settings.vport_margin
        if self.left:
            return (0, y)
        return (self.tcd.width, y)


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
        transform = self.vpp.tcd.tlblock.transform
        if isinstance(transform, svg.Translate):
            x_offset, y_offset = transform.x, transform.y
            assert isinstance(x_offset, (float, int))
            assert isinstance(y_offset, (float, int))
        else:
            x_offset = y_offset = 0
        y += y_offset

        if self.left:
            return (x_offset + self.vpp.tcd.tlblock.width, y)
        return (x_offset, y)


class SiblingVirtualPortPoint(Point):
    """Point for a virtual port on a sibling subtimeline (of the same owning
    component), expressed relative to `frame`'s own coordinate frame -- for a route
    drawn as part of `frame`'s own rendering that needs to reach into a sibling
    instead (see ComponentBlock.sibling_point_for).
    """

    def __init__(
        self, frame: "TimelineBlock", tcd: "TopConduitDuct", index: int, left: bool
    ) -> None:
        self.frame = frame
        self.point = VirtualPortPointInDuct(tcd, index, left)

    def __call__(self) -> tuple[float, float]:
        x, y = self.point()
        transform = self.frame.transform
        assert isinstance(transform, svg.Translate)
        fx, fy = transform.x, transform.y
        assert isinstance(fx, (float, int))
        assert isinstance(fy, (float, int))
        return (x - fx, y - fy)


AnyLane = Lane | CruiseLane


def _apply_lanes(
    x: float, y: float, lanes: list[AnyLane], dest_y: float | None = None
) -> tuple[list[svg.PathData], float, float, bool]:
    """Apply `lanes` in order starting from (x, y) (see CruiseLane for what makes a
    lane actually used or skipped, and where its y comes from), returning the SVG
    H/V commands for the ones actually used, the (x, y) reached after them, and
    whether the last one used was horizontal (needed to know which way the final
    bend into a route's destination should go -- see ConduitRoute.to_svg).

    `dest_y` is required if `lanes` contains a CruiseLane with `clamp_to_dest=True`
    (there is no destination to resolve one against otherwise).

    Shared with LanePoint, which needs just the resulting (x, y): both a route drawn
    from scratch and one continuing on from a shared trunk drawn by another route
    (see TopConduitDuct.route_conduits) resolve lanes the same way.
    """
    path: list[svg.PathData] = []
    last_horizontal = False
    for lane_or_cond in lanes:
        if isinstance(lane_or_cond, CruiseLane):
            duct = lane_or_cond.duct
            if not duct.cruise_needed(lane_or_cond):
                # Nothing of this row to clear: the route stays inside one duct.
                continue
            # Skip this cruise if we're already at a y that's safe for the row's
            # *entire* width -- i.e. `y` here, not the final destination's: a
            # skipped cruise leaves the route traveling at whatever y it already
            # has (nothing after this sets y again until the final bend into the
            # destination's own reserved x, which is always safe), so it's that
            # current y -- not where the route eventually ends up -- that has to
            # be safe for the width still ahead of it. Recorded as used either
            # way, so a cruise that *does* bend below never lands right on top of
            # one that skipped and is already cruising at its own, unrelated y.
            #
            # y == 0 never qualifies for this skip, even though it's always <=
            # duct.height: it's not just *a* safe height, it's the row's own top
            # edge -- exactly where every one of this row's own top components'
            # O_I ports already sits (see TopConduitDuct.route_conduits), so
            # travelling there is never actually clear of them, just clear of
            # the *components* lower down that this check is about.
            #
            # A shared bus (shared_lane set) resolves once and caches the answer,
            # skipped or not, so every branch that reuses it agrees on the exact same
            # y regardless of when each one gets resolved. calc_layout relies on this
            # by reserving a single height per bus.
            key = id(lane_or_cond.shared_lane) if lane_or_cond.shared_lane else None
            if key is not None and key in duct._hlane_resolved:
                new_y = duct._hlane_resolved[key]
            elif 0 < y <= duct.height:
                if key is not None:
                    duct._hlane_resolved[key] = y
                duct._cruise_heights_used.append(y)
                continue
            else:
                # See TopConduitDuct.safe_cruise_height for what this avoids running
                # through (or too close to): both the row's own components and any
                # other conduit already cruising through this row.
                if lane_or_cond.clamp_to_dest:
                    assert dest_y is not None
                    new_y = duct.safe_cruise_height(below=dest_y)
                else:
                    new_y = duct.safe_cruise_height()
                if key is not None:
                    duct._hlane_resolved[key] = new_y
                duct._cruise_heights_used.append(new_y)
            if new_y == y:
                continue
            last_horizontal = True
            y = new_y
            path.append(svg.V(y))
            continue
        lane = lane_or_cond
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
    """The point reached by starting at `origin` and following `lanes` (see
    _apply_lanes) -- i.e. wherever a shared trunk (drawn once as its own
    ConduitRoute, from `origin` through `lanes`) actually ends. Conduits that share
    that trunk use this as their *own* origin, with only their individual remaining
    lanes, so the trunk itself is drawn exactly once instead of being redrawn as an
    identical, overlapping prefix by every conduit that starts with it (see
    TopConduitDuct.route_conduits). `lanes` here must not contain a CruiseLane with
    `clamp_to_dest=True` -- there's no destination yet to resolve one against.
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
    """Force the path's very last drawn segment into `destination` to be a
    horizontal (True) or vertical (False) move, overriding whatever orientation the
    lanes above would otherwise leave it ending on. Use True for a PortPoint
    destination: an F_INIT/O_F port's own shape (see svg_builder's
    port-f_init/port-o_f defs) is a chevron that only reads correctly approached
    horizontally, but which orientation the lanes happen to leave the route ending
    on depends on unrelated routing choices (e.g. whether a shared cruise lane --
    see CruiseLane -- was needed or skipped for this particular conduit), not
    on the destination itself. None (the default) infers it from the lanes as
    before, for destinations without that requirement."""

    def to_svg(self) -> svg.Path:
        """Create an SVG Path to describe this conduit route."""
        x, y = self.origin()
        path: list[svg.PathData] = [svg.M(x, y)]
        dest_x, dest_y = self.destination()
        # With no lanes at all (origin and destination already line up through a
        # sibling hop with nothing local left to traverse, see
        # ComponentBlock.sibling_point_for), go straight from origin to destination --
        # default to vertical-then-horizontal (down, then across), as if coming off a
        # vertical lane, rather than requiring at least one real lane just to know
        # which way to bend.
        lane_path, x, y, last_horizontal = _apply_lanes(x, y, self.lanes, dest_y)
        path.extend(lane_path)
        if self.final_ends_horizontal is not None:
            # last_horizontal drives which of H/V is emitted *last* below, so it's
            # the negation of what the path's own final segment ends up being.
            last_horizontal = not self.final_ends_horizontal
        # Only emit a final H/V if it actually moves the pen: a zero-length one
        # (destination already reached exactly, e.g. a LanePoint another route
        # picks up from -- see route_conduits) would still render as a visible
        # square-capped stub (settings' stroke-linecap: square applies at the true
        # end of every path, including a zero-length final segment), making the
        # join between the two routes look thicker than the single continuous line
        # it's meant to be.
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
        self._hlanes = Lanes(horizontal=True)
        """Main horizontal lanes, for all conduits going left -> right."""

        self._routes: list[ConduitRoute] = []
        """List of conduit routes through this timeline."""
        self._entry_points: dict[Reference, Point] = {}
        """Per-sender cache of where that sender's shared entry (_hlanes_for_oi, if
        any, then vlanes_in[0] -- common to every conduit from that sender
        regardless of its own destination) ends (see route_conduits), for conduits
        from a broadcast sender to continue from instead of redrawing that entry as
        their own overlapping prefix."""
        self._trunk_points: dict[Reference, Point] = {}
        """Per-sender cache of where that sender's shared cruise lane ends, for the
        subset of a broadcast sender's conduits that also share that (see
        route_conduits's "B" branch and _entry_points above, which this builds on
        top of)."""
        self._cruise_heights_used: list[float] = []
        """y's that a CruiseLane has already actually cruised at (or, skipped,
        already found itself safely at) while rendering this row (see
        safe_cruise_height/_apply_lanes) -- so an *unrelated* later conduit's own
        cruise, computed independently, doesn't happen to land on the exact same
        y an earlier one already settled on (both avoiding, say, the same
        _hlanes lane and so both landing on the one spot just clear of it) and
        collide with it in turn."""
        self._vport_y: dict[tuple[bool, int], float] = {}
        """Explicit y overrides for specific (left, index) virtual ports (see
        add_virtual_port's `y` and VirtualPortPoint.__call__) -- a virtual port
        without an entry here just uses the regular index-based formula."""
        self._hlane_resolved: dict[int, float] = {}
        """Per shared-bus CruiseLane (keyed by id() of its own `shared_lane`, see
        _apply_lanes) cache of the y it actually resolved to via
        safe_cruise_height() -- computed once, the first time any route reaches
        it, and reused after that so a shared sender's trunk (see _join_trunk),
        whose lane multiple routes reference, settles on the exact same height
        everywhere it's used."""
        self._crossing_buses: set[int] = set()
        """id() of the `shared_lane` of every shared bus with at least one branch that
        passes over one of this row's components (see cruise_needed)."""

    def _cruise(
        self,
        shared_lane: Lane | None = None,
        clamp_to_dest: bool = False,
        crosses: bool = True,
    ) -> CruiseLane:
        """Create a CruiseLane in this row, registering its shared bus as crossing if
        this branch crosses."""
        if crosses and shared_lane is not None:
            self._crossing_buses.add(id(shared_lane))
        return CruiseLane(self, shared_lane, clamp_to_dest, crosses)

    def cruise_needed(self, lane: CruiseLane) -> bool:
        """Whether `lane` can pass over one of this row's components, either itself
        or through another branch of its shared bus."""
        if lane.crosses:
            return True
        return lane.shared_lane is not None and id(lane.shared_lane) in (
            self._crossing_buses
        )

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
            key: Group conduits under this reference instead of conduit.sender -- e.g.
                to merge several different senders heading to the same destination
                into one shared port/lane.
            y: Place this port at this exact y (see VirtualPortPoint.__call__)
                instead of the regular index-based one -- for a conduit that's
                simply continuing on through this duct at a y it already reached
                elsewhere (see route_conduits), so it doesn't need its own bend
                just to land on an unrelated, independently numbered position here.
                Ignored (falls back to the regular formula) if it would coincide
                with another port already placed this way on the same side, since
                that would draw the two straight on top of each other instead.
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

        N.B. these must stay in lockstep with vlanes_in[0]/ducts[-1].vlanes_out's
        own positions (see _get_input_conduits/route_conduits's "B" branch): an
        entering or exiting conduit's straight line up into this port relies on
        the port sitting exactly where that lane already does, so nudging
        oi_offset/s_offset off that -- e.g. to make the two sides visually
        symmetric -- reintroduces the jog that alignment was there to avoid.

        ducts[-1] (an O_F port's own exit) puts vlanes_out first (see
        ConduitDuct.calc_layout), i.e. right at ducts[-1]'s own left edge, so
        aligning with it is just ducts[-1]'s own width back from the right edge --
        exact, regardless of whatever vlanes_transfer/vlanes_in the same duct also
        carries for other conduits passing through it (and regardless of any
        trailing padding TimelineBlock.calc_layout may have added to it to center
        a lone component in its row, since that padding is baked into this same
        width -- and so into seg_right derived from it -- consistently on both
        sides of this subtraction). ducts[0] (an F_INIT port's own entry) puts
        vlanes_in *last* instead, so the matching offset there is whatever comes
        before it in that same duct specifically -- unlike ducts[-1], *not* just
        "ducts[0].width minus vlanes_in's own width", since that width may
        likewise include trailing padding (after vlanes_in, not before it) that
        would otherwise get miscounted as leading space -- at least enough to
        also clear left_vports' own fan-in, if that happens to need more.
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
        """Whether every sender using ducts[0].vlanes_in is one of
        top_components[0]'s own O_I ports for this timeline (see route_conduits/
        the reservation loop at its top) -- as opposed to also carrying a conduit
        arriving from elsewhere, e.g. a sibling's own broadcast hopping through
        this same row (see ComponentBlock.sibling_point_for). Everything sharing
        this bundle in the "own ports only" case still gets its own, individually
        correct lane position (unconnected ports included, reserved but never
        drawn -- see the reservation loop), so shifting where the bundle as a
        whole starts (see TimelineBlock.calc_layout's leading_pad) moves all of
        them together and disturbs nothing else's alignment. A sender from
        elsewhere has its own, separately-positioned origin that doesn't move
        just because this does, so that case has to fall back to leaving
        vlanes_in's own start alone (see TimelineBlock.calc_layout).
        """
        if not self.top_components:
            return False
        own_ports = set(
            self.top_components[0].ports_per_operator(Operator.O_I, self.timeline)
        )
        return set(self.ducts[0].vlanes_in.keys()) <= own_ports

    def own_s_port_count(self) -> int:
        """Number of S ports top_components[-1] has for this timeline (see
        _place_subtimeline).

        S ports are laid out left-to-right starting at x0 = seg_right +
        s_offset (see port_offsets/_place_subtimeline), so index 0 -- the one
        s_offset actually aligns with vlanes_out[0] (see port_offsets) -- ends
        up *farthest* from the component's own right edge, and the *last*
        index ends up nearest it instead. O_I ports lay out the same
        direction, but since they start near the *left* edge, index 0 there
        already ends up nearest to it -- no equivalent correction needed. This
        count is that correction: how many port_margins short of the true
        nearest-port distance s_offset's own alignment target leaves things,
        for TimelineBlock.calc_layout to close when balancing where the O_I
        and S groups' own nearest port ends up relative to their own edge.
        """
        if not self.top_components:
            return 0
        return len(
            list(self.top_components[-1].ports_per_operator(Operator.S, self.timeline))
        )

    def safe_cruise_height(self, below: float | None = None) -> float:
        """A y a CruiseLane may cruise at in this row: clear of the row's own
        components (see calc_layout), and clear of every other cruise already
        actually drawn here too (with at least settings.hlane_margin either way).

        _cruise_heights_used holds the y's that earlier CruiseLanes in this row
        actually settled on (see _apply_lanes, which records one whether it
        skipped -- already safe at its own current y -- or genuinely cruised) --
        every one of those is real, so all of them are checked here.

        Without `below`, this is the topmost clear y: the band fills top-down, so
        the first cruise drawn (from the leftmost lanes, going furthest right) is
        highest and later ones pass underneath it without crossing its vertical
        lanes. With `below` (see CruiseLane.clamp_to_dest), this is the lowest clear
        y at or above `below`, so the route stays as close as possible to its
        destination's y. calc_layout reserves enough height (one hlane_margin per
        cruise, plus one) for either to stay within 0..height.
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
        """Get the point where `sender`'s shared bus currently reaches, creating it
        (starting from `origin`) the first time any of its conduits needs it.

        Every conduit sharing `sender` joins this same, single bus in turn (see
        route_conduits) -- each one continuing from wherever the *previous* one
        left it (updated in route_conduits's "B" branch whenever one of them
        actually extends the bus further right), not from `origin` again, so the
        whole broadcast reads as one continuous line with each destination
        branching off it, rather than every destination independently reaching
        back to the start.

        Since every later conduit reuses this same memoized point instead of
        deciding its own cruise height, the bus only needs to bend down to
        `_hlanes[sender]`'s dedicated row if `origin` itself isn't already safe to
        cruise at (see CruiseLane) -- unlike a plain per-conduit CruiseLane,
        there's no other conduit here that might independently need the bend, so
        nothing is left un-merged by skipping it. Forcing the bend regardless
        would draw it even when `origin` is already clear (e.g. a virtual port's
        own height), landing the trunk a few pixels below the lane it just
        entered on for no reason.
        """
        trunk_point = self._trunk_points.get(sender)
        if trunk_point is None:
            # Whether the trunk is needed depends on its branches (see cruise_needed)
            trunk_lanes: list[AnyLane] = [
                self._cruise(shared_lane=self._hlanes[sender], crosses=False)
            ]
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

    def _sibling_point_for(self, conduit: Conduit) -> tuple[list[Lane], Point] | None:
        """See ComponentBlock.sibling_point_for -- None if we're not one of a
        component's own subtimelines (e.g. we're the root timeline)."""
        if not self.top_components:
            return None
        return self.top_components[0].sibling_point_for(self.tlblock, conduit)

    def _route_to_sibling_or_parent(
        self,
        conduit: Conduit,
        preferred_y: float | None = None,
        lane_key: Reference | None = None,
    ) -> tuple[list[Lane], Point]:
        """Resolve a conduit with no destination in this timeline: hand it to a
        sibling subtimeline one hop closer (see ComponentBlock.sibling_point_for), or
        if it genuinely exits to our parent timeline, (re)group it by receiver so
        conduits from different senders heading to the same destination share a lane
        instead of each getting their own.

        preferred_y: the y this conduit is already at, if that's known before
            layout (see route_conduits) -- passed through to the genuine-exit
            port's own placement (see add_virtual_port's `y`) so a conduit simply
            passing through this timeline keeps traveling in a straight line
            instead of bending to some unrelated, independently numbered exit
            height. Unused for a sibling hop, which always has its own fixed
            landing point on the sibling's side (see ComponentBlock.
            sibling_point_for) that this timeline's own y has no say in.
        lane_key: key of the exit lane in ducts[-1], instead of conduit.receiver --
            see _shares_sending_port.
        """
        sibling = self._sibling_point_for(conduit)
        if sibling is not None:
            return sibling
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
            True if conduit.sender has more than one outgoing conduit here (e.g. a
            "repeat" broadcast) -- see route_conduits, which only bothers merging a
            conduit onto a shared trunk lane with its sender's other conduits when
            there's actually another one to share it with.
        """
        # Conduits coming from top components
        idx = len(self.top_components)
        for component in reversed(self.top_components):
            idx -= 1
            for conduit in component.conduits_per_operator(
                Operator.O_I, self.timeline, reversed=True
            ):
                origin = PortPoint(component, conduit.sending_port())
                port_conduits = component.conduits_per_port[conduit.sending_port()]
                yield (idx, origin, conduit, len(port_conduits) > 1)
        # Conduits coming from the parent timeline
        if self.left_conduit_duct is None:
            # Reserve vlanes of model ports so that the top-most port gets the
            # right-most lane (vlanes_in is numbered in reverse). These ports are
            # spaced closer (vport_margin) than the F_INIT ports below them that they
            # go down to, so the other way around every conduit would cross the
            # vertical lane of the one above it.
            for name in self.left_vports:
                self.ducts[0].vlanes_in[name]
        # Then assign conduits in regular order
        for idx, conduits in enumerate(self.left_vports.values()):
            origin = VirtualPortPoint(self, idx, left=True)
            for conduit in conduits:
                yield (-1, origin, conduit, len(conduits) > 1)

    def _get_duct_conduits(
        self,
    ) -> Iterator[tuple[int, Reference, list[Lane], Point, Conduit]]:
        """Iterator over all conduits connected to ConduitDucts.

        Yields:
            (duct_index, key, extra_lanes, origin_point, conduit) for each conduit,
            starting with conduits connected to the right-most port. extra_lanes
            contains any lanes needed to route the conduit past subtimelines between
            its origin and the duct. key is the reference lanes local to this timeline
            should be grouped by for this conduit -- see ConduitDuct.get_conduits.
        """
        for idx, duct in enumerate(self.ducts):
            for key, extra_lanes, origin, conduit in duct.get_conduits():
                yield (idx, key, extra_lanes, origin, conduit)

    def _finish_bottom_route(
        self, route_origin: Point, idest: int, lanes: list[AnyLane], conduit: Conduit
    ) -> None:
        """Append the route once `lanes` has been extended up to ducts[idest]'s own
        entry (see route_conduits's two "B" branches, which share this tail):
        resolve the actual destination there, and force a horizontal final
        approach into a PortPoint destination -- a "B" destination that lands on a
        real component is always its F_INIT port, whose chevron shape (see
        ConduitRoute.final_ends_horizontal) only reads correctly approached
        horizontally, regardless of which lane above happened to be used last for
        this conduit. A destination inside a nested subtimeline instead (not a
        PortPoint) has no such requirement.
        """
        entry_lanes, dest = self.ducts[idest].get_point_for(conduit)
        lanes.extend(entry_lanes)
        final_ends_horizontal = True if isinstance(dest, PortPoint) else None
        self._routes.append(ConduitRoute(route_origin, dest, lanes, final_ends_horizontal))

    def route_conduits(self) -> None:
        """Route all conduits inside this timeline and all subtimelines."""
        if not self._destinations:
            self._fill_destinations()

        if self.top_components:
            # Reserve space for all O_I ports in the first component
            for port in self.top_components[0].ports_per_operator(
                Operator.O_I, self.timeline, reversed=True
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
                    # A conduit sent directly by one of this row's own
                    # components reaches ducts[-1].vlanes_out keyed by its own
                    # sender (see ConduitDuct.get_conduits' ComponentBlock
                    # branch) -- but one arriving from deeper down, forwarded
                    # hop by hop up through a nested subtimeline (see
                    # _route_to_sibling_or_parent), reaches it keyed by its
                    # receiver instead (the key that forwarding preserves
                    # end to end). Reserving under the wrong one of the two
                    # doesn't just waste a slot -- since it doesn't match the
                    # key routing actually uses, it adds a second, real entry
                    # alongside the reserved one, throwing off every S port
                    # positioned after it (see port_offsets).
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
            # Always reserve ducts[0]'s own vlanes_in slot for this sender, whether
            # or not this particular conduit ends up using it as a lane below, so
            # every other sender's own reserved position here (this bundle numbers
            # entries in reverse -- see Lanes(reversed=True) -- so one sender's
            # position shifts with how many others are reserved after it) stays
            # exactly where it already was, independent of this decision.
            vlane_in_0 = self.ducts[0].vlanes_in[conduit.sender]
            if shares_sender or destination is not None:
                # That lane is where a "B" destination's own entry (get_point_for,
                # below) picks up from, so it's needed whenever this conduit
                # actually terminates somewhere in this row -- but a lone conduit
                # that's simply hopping through (destination is None, see below)
                # never visits ducts[0] at all, so lining up with its lane first
                # would only add a horizontal jog before the vertical bend that's
                # actually needed, for no reason. Still included whenever shared:
                # some other conduit from the same sender might be a "B"
                # destination that does need this shared entry to align with it
                # (see below).
                lanes.append(vlane_in_0)
            route_origin = origin
            if shares_sender:
                # Every conduit from this sender starts with this same entry (the
                # lanes gathered above -- _hlanes_for_oi, if any, then vlanes_in[0])
                # regardless of which destination each one ends up with below --
                # draw it once, the first time we see this sender, and have every
                # conduit that shares it continue on from where it left off (see
                # LanePoint) instead of each redrawing it as its own identical,
                # overlapping prefix.
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
                    # Join this sender's shared bus here too (see _join_trunk), so
                    # a hop reads as one more branch off the same broadcast rather
                    # than an unrelated line reaching all the way back to the
                    # start -- continuing from wherever a "B" destination above
                    # last left it, accepting whatever small final correction (see
                    # ConduitRoute.to_svg) that ends up needing to actually reach
                    # this hop's own fixed landing point.
                    route_origin = self._join_trunk(conduit.sender, route_origin)
                    # The hop travels at the trunk's height, across the whole row
                    self._crossing_buses.add(id(self._hlanes[conduit.sender]))
                else:
                    # A plain shared-bus CruiseLane wouldn't do here: a sibling
                    # subtimeline's own entry point (or a genuine exit's own
                    # virtual port) already gives this route a fixed, specific
                    # landing y of its own, unrelated to this timeline's _hlanes
                    # numbering -- cruising at some other, unrelated y first would
                    # just add a disconnected detour that has to be corrected
                    # again right where it lands. Align with that landing y
                    # instead (clamped to only as much altitude as actually
                    # needed) so the route travels in a single straight line
                    # wherever that's safe.
                    lanes.append(self._cruise(clamp_to_dest=True))
                    if isinstance(origin, VirtualPortPoint):
                        # This conduit is itself just passing through (it arrived
                        # here as a hop from a previous sibling, see
                        # ComponentBlock.sibling_point_for) -- its own y is
                        # already fixed and, unlike a real component's port,
                        # knowable before layout runs (see VirtualPortPoint), so
                        # if it's a genuine exit below (not a further sibling
                        # hop, which always has its own fixed landing point
                        # regardless), let it leave at that same y instead of an
                        # unrelated, independently numbered one -- keeping the
                        # CruiseLane above a no-op (see its own skip) and this
                        # whole row a single straight line for it.
                        preferred_y = origin()[1]
                hop_lanes, dest = self._route_to_sibling_or_parent(conduit, preferred_y)
                lanes.extend(hop_lanes)

            elif destination[0] == "B":  # Route to a Bottom destination
                idest = destination[1]
                if shares_sender:
                    # B1/B2 (etc.) additionally share the cruise lane itself, on
                    # top of the shared entry above -- draw that once too, the
                    # first time we see this sender reach here.
                    route_origin = self._join_trunk(conduit.sender, route_origin)
                # Every branch off this sender -- whether it's the one that joined
                # the trunk just above or a lone conduit that never shared one --
                # resolves the same per-sender shared-bus CruiseLane, so a
                # broadcast's further destinations get the same collision-aware
                # cruise-height check a lone conduit already gets, instead of just
                # trusting the trunk's already-fixed y all the way to their own,
                # possibly much further, entry point. Only a destination past
                # ducts[0] actually passes over one of this row's components.
                lanes.append(
                    self._cruise(
                        shared_lane=self._hlanes[conduit.sender], crosses=idest > 0
                    )
                )
                if idest > 0:
                    extend_lane = self.ducts[idest].vlanes_in[conduit.sender]
                    lanes.append(extend_lane)
                    if shares_sender:
                        # This conduit pushes the shared bus itself further right
                        # to reach its own destination -- so the *next* one to
                        # join it (see _join_trunk) continues from here, instead
                        # of from wherever the bus was before this conduit came
                        # along.
                        self._trunk_points[conduit.sender] = LanePoint(
                            route_origin, [extend_lane]
                        )
                self._finish_bottom_route(route_origin, idest, lanes, conduit)
                continue

            elif destination[0] == "T":  # Route to a Top destination
                continue  # TODO, interact coupling

            route = ConduitRoute(route_origin, dest, lanes)
            self._routes.append(route)

        # Route conduits coming from internal components and subtimelines. Lanes local
        # to this timeline are grouped by `key` throughout (see ConduitDuct.
        # get_conduits): usually conduit.sender, so e.g. a multicast shares a lane
        # right up to where each receiver actually differs, but conduits already
        # forwarded on from a subtimeline keep whatever key grouped them there.
        for iduct, key, extra_lanes, origin, conduit in self._get_duct_conduits():
            destination = self._destinations.get(conduit.receiving_component())
            # A fresh copy: extra_lanes may now be shared by several conduits in the
            # same group (see ConduitDuct.get_conduits), so appending to it below must
            # not mutate what the other conduits in that group see.
            lanes: list[AnyLane] = list(extra_lanes)
            if destination is None:  # Route to a sibling subtimeline, or the parent
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[key])
                    # See the matching branch in the loop above: align with the
                    # route's own landing y (clamped to what's actually safe)
                    # instead of an unrelated cruise altitude.
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
                    # See the "None" branch above and CruiseLane: skips the bend
                    # if this conduit's own origin is already safe, and otherwise
                    # resolves to a height consistent with every other cruise in
                    # this row (see _apply_lanes) instead of an unrelated,
                    # independently-numbered one.
                    lanes.append(self._cruise(shared_lane=self._hlanes[key]))
                    lanes.append(self.ducts[idest].vlanes_in[key])
                self._finish_bottom_route(origin, idest, lanes, conduit)
                continue

            else:  # Route to a Top destination
                idest = destination[1]
                if iduct != len(self.ducts) - 1:
                    lanes.append(self.ducts[iduct].vlanes_out[key])
                    # See the "B" branch above.
                    lanes.append(self._cruise(shared_lane=self._hlanes[key]))
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
        # Every CruiseLane resolves dynamically through safe_cruise_height at
        # render time (see _apply_lanes) rather than through a statically
        # pre-assigned position -- _hlanes itself is (as of this dynamic
        # resolution) only ever used to hand a shared-bus CruiseLane a stable
        # per-sender identity to cache its own resolved height against (see
        # route_conduits/_apply_lanes's `key = id(...)`), never read for an
        # actual position, so it contributes nothing to this row's height on its
        # own. Reserve room for every route that has a CruiseLane instead
        # (routing is already done by now -- see ModelBlock.__init__ -- so every
        # one of them already exists in self._routes): every one of them
        # (skipped or not) pushes the *next* one further away in turn (see
        # _cruise_heights_used), so without reserving room for every one of them
        # here, safe_cruise_height runs out of room once whatever
        # _hlanes_for_s/_hlanes_for_oi themselves reserved is used up, and ends
        # up clamping to a negative y -- back up into whatever is above this row
        # -- instead. Counting every route that merely *has* one of these
        # (whether or not it turns out to actually skip) is deliberately
        # worst-case: cheaper than tracking which ones will skip, which isn't
        # knowable until render time anyway.
        #
        # N such routes need (N+1) * hlane_margin, not N: a skip can pin
        # itself at any y up to (but never past) this row's own height -- that
        # *is* the skip condition -- so in the worst case, N-1 of them end up
        # pinned back-to-back just hlane_margin apart starting from the very
        # top, leaving the last one needing a full extra hlane_margin below
        # the lowest of those to still land at or above 0 itself.
        #
        # Cruises that can't pass over any of this row's components (see
        # cruise_needed) are never drawn, so they don't count. All cruises of one
        # shared bus resolve to a single y (see _apply_lanes), so a bus counts once.
        needed = [
            lane
            for route in self._routes
            for lane in route.lanes
            if isinstance(lane, CruiseLane) and self.cruise_needed(lane)
        ]
        buses = {id(lane.shared_lane) for lane in needed if lane.shared_lane}
        cruising_lanes = len(buses) + sum(1 for lane in needed if not lane.shared_lane)
        if cruising_lanes:
            height += (cruising_lanes + 1) * settings.hlane_margin
        # Also reserve enough height for every left/right virtual port (see
        # VirtualPortPoint) to land at a y this row's own safe_cruise_height
        # already clears. Those ports sit on this duct's own left/right edge and
        # are free to be at any y -- but a route that has to *cross* this row to
        # reach one (see route_conduits's "None" branch, CruiseLane) only skips
        # its own cruise-then-correct bend if that port's y is already safe (see
        # safe_cruise_height) -- otherwise it cruises at whatever
        # shorter height *is* safe first, then has to bend again right at the end
        # to correct down to the port's real position, for no reason a route with
        # nothing to cross wouldn't also need. Sizing this row to clear the
        # farthest port on either side keeps every such crossing route to the one
        # bend it actually needs, same as one that had nothing to cross at all.
        # Without any crossing route there's nothing for this to help.
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
        self.vlanes_in = Lanes(horizontal=False, reversed=True)
        """Vertical lanes, carrying conduits from top to right."""
        self._leading_pad: float = 0.0
        """Extra blank space calc_layout was last given before vlanes_in (see
        calc_layout's own leading_pad/width_before_vlanes_in)."""

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

    def get_point_for(self, conduit: Conduit) -> tuple[list[Lane], Point]:
        """Get a Point to describe the position of the destination of the conduit."""
        idx = self._destinations[conduit.receiving_component()]
        connector = self.right_connectors[idx]
        if isinstance(connector, ComponentBlock):
            return [], PortPoint(connector, conduit.receiving_port())
        else:
            owner = connector.top_components[0]
            # Always enter via the first (duct-adjacent) subtimeline. If the
            # destination actually lives further along, that subtimeline's own
            # routing hands the conduit to its sibling (see
            # ComponentBlock.sibling_point_for), sharing lanes with whatever it
            # already needs for that sender, instead of a dedicated pass lane.
            connector = owner.subtimelines[0].top_conduit_duct
            vidx = connector.add_virtual_port(conduit, left=True)
            point = VirtualPortPointInDuct(connector, vidx, left=False)
            lanes = owner.pass_lanes_to_duct(
                connector.tlblock, conduit.sender, duct_on_left=True
            )
            return lanes, point

    def get_conduits(
        self,
    ) -> Iterator[tuple[Reference, list[Lane], Point, Conduit]]:
        """Iterator over all conduits that enter this conduit duct from left_connectors.

        Yields:
            (key, extra_lanes, origin_point, conduit) for each conduit. key is the
            reference that lanes local to the *receiving* timeline should be grouped
            by for this conduit -- conduit.sender for a conduit fresh from a real
            component, or the key it was already grouped under if it's being forwarded
            on from a subtimeline (see TopConduitDuct.add_virtual_port), so that
            grouping is preserved across multiple hops instead of reverting to
            per-sender lanes at each one.
        """
        for left_connector in self.left_connectors:
            if isinstance(left_connector, ComponentBlock):
                for conduit in left_connector.conduits_per_operator(Operator.O_F):
                    origin = PortPoint(left_connector, conduit.sending_port())
                    yield conduit.sender, [], origin, conduit
            else:
                left_connector.route_conduits()
                owner = left_connector.top_components[0]
                # Loop over conduits coming in from the child timeline. Each group's
                # own key (not necessarily conduit.sender -- see add_virtual_port)
                # decides which crossing lane the whole group shares.
                for idx, (key, conduits) in enumerate(
                    left_connector.right_vports.items()
                ):
                    origin = VirtualPortPointInDuct(left_connector, idx, left=True)
                    extra_lanes = owner.pass_lanes_to_duct(
                        left_connector.tlblock, key, duct_on_left=False
                    )
                    for conduit in conduits:
                        yield key, extra_lanes, origin, conduit

    def lane_width(self) -> float:
        """Spacing between this duct's own vlanes (see calc_layout): port_margin at
        either outer edge of a timeline (so the lanes there line up with the O_I/S
        ports of the component above -- see TopConduitDuct.port_offsets),
        conduit_margin between two real components."""
        if not self.left_connectors or not self.right_connectors:
            return settings.port_margin
        return settings.conduit_margin

    def width_before_vlanes_in(self) -> float:
        """Width of vlanes_out + vlanes_transfer, plus any leading_pad calc_layout
        was given -- i.e. everything that comes before vlanes_in in this duct (see
        calc_layout) -- the offset an entering O_I port (see TopConduitDuct.
        port_offsets) needs from this duct's own left edge to land exactly on
        vlanes_in[0]."""
        lanes_width = (len(self.vlanes_out) + len(self.vlanes_transfer)) * self.lane_width()
        return lanes_width + self._leading_pad

    def calc_layout(self, leading_pad: float = 0.0) -> None:
        """Calculate size and layout of this component.

        Args:
            leading_pad: Extra blank space to insert right before vlanes_in
                (after vlanes_out/vlanes_transfer) -- widening this duct from
                the middle instead of trailing after everything, so an entering
                O_I port aligned with vlanes_in[0] (see width_before_vlanes_in/
                TopConduitDuct.port_offsets) can be pushed out to line up with
                a sibling duct elsewhere, e.g. to keep O_I and S port groups a
                consistent distance from their own component's edges across
                different nesting levels (see TimelineBlock.calc_layout).
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
