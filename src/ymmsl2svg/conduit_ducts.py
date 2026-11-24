import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import svg

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.conduit_router import ConduitNet
    from ymmsl2svg.timeline_block import TimelineBlock


Side = Literal["left", "right", "up", "down"]
"""Direction in which a conduit leaves a lane, perpendicular to the lane."""


@dataclass(frozen=True)
class Attachment:
    """Point where a net enters or leaves a duct."""

    key: float
    """Position along the duct, only used to order attachments (not a coordinate): y
    order for a ConduitDuct, x order for a TopConduitDuct (see ConduitRouter)."""
    side: Side | None
    """Side the net leaves its lane to, None if it continues along the lane out of the
    end of the duct."""
    via: "Duct | None" = None
    """Duct that the net goes into here, if the order of its lanes refines the order of
    the attachments: the lanes of a column below a row, or of a row below a component
    next to a column. Lanes must be assigned in that duct first."""


@dataclass(eq=False)
class Lane:
    """A lane in a duct, holding one or more nets that don't overlap."""

    duct: "Duct"
    """Duct this lane is in."""
    index: int
    """Index in the duct: 0 is the left-most (ConduitDuct) or top-most
    (TopConduitDuct) lane."""
    nets: list["ConduitNet"] = field(default_factory=list)
    """Nets using this lane."""

    @property
    def offset(self) -> float:
        """Distance of this lane from the left/top edge of its duct."""
        return (self.index + 0.5) * settings.lane_margin

    @property
    def local_pos(self) -> float:
        """x (ConduitDuct) or y (TopConduitDuct) coordinate in the frame of the duct."""
        start = self.duct.y if self.duct.horizontal else self.duct.x
        return start + self.offset

    @property
    def pos(self) -> float:
        """x (ConduitDuct) or y (TopConduitDuct) coordinate in the model's frame."""
        x, y = self.duct.frame_origin
        return self.local_pos + (y if self.duct.horizontal else x)


class Duct(SvgBlock):
    """Base class for ducts: a set of parallel lanes that nets are assigned to."""

    horizontal: bool
    """Direction of the lanes."""
    before: Side
    """Side towards lane 0: a net leaving its lane to this side passes all lanes with a
    lower index."""
    after: Side
    """Side towards the last lane."""

    def __init__(self, frame: "TimelineBlock | None") -> None:
        super().__init__()
        self.frame = frame
        """Timeline whose frame self.x and self.y are in, None for the model's frame."""
        self.lanes: list[Lane] = []
        """Lanes, see assign_lanes."""
        self._attachments: dict[ConduitNet, list[Attachment]] = {}
        """Where each net enters/leaves this duct."""
        self._lane_of: dict[ConduitNet, Lane] = {}
        self._preferred: dict[ConduitNet, int] = {}
        """Preferred lane index per net, see assign_lanes."""
        self._spans: dict[ConduitNet, tuple[float, float]] = {}
        """Cache for _span."""

    @property
    def frame_origin(self) -> tuple[float, float]:
        """Position of the frame of this duct, in the frame of the model."""
        return self.frame.origin if self.frame is not None else (0, 0)

    @property
    def size(self) -> float:
        """Width (ConduitDuct) or height (TopConduitDuct) needed for all lanes."""
        return len(self.lanes) * settings.lane_margin

    def attach(self, net: "ConduitNet", attachment: Attachment) -> None:
        """Register that `net` enters or leaves this duct at `attachment`."""
        self._attachments.setdefault(net, []).append(attachment)

    def lane_of(self, net: "ConduitNet") -> Lane:
        """Lane assigned to a net."""
        return self._lane_of[net]

    def _key(self, net: "ConduitNet", attachment: Attachment) -> float:
        """Order of an attachment of a net, refined by its lane in attachment.via."""
        if attachment.via is None:
            return attachment.key
        return attachment.key + (attachment.via.lane_of(net).index + 0.5) * 1e-3

    def _span(self, net: "ConduitNet") -> tuple[float, float]:
        """First and last attachment of a net along the duct (cached while assigning
        lanes, as the lanes of attachment.via don't change by then)."""
        span = self._spans.get(net)
        if span is None:
            keys = [self._key(net, a) for a in self._attachments[net]]
            span = self._spans[net] = (min(keys), max(keys))
        return span

    def assign_lanes(
        self,
        preferred: dict["ConduitNet", int] | None = None,
        pinned: list[tuple["ConduitNet", int]] | None = None,
    ) -> None:
        """Assign a lane to each net, using as few lanes and crossings as possible.

        Pinned nets come first, each in the lane with its index, or the first lane
        after it that it doesn't overlap with other pinned nets in. For the other nets,
        first each net gets its own lane. These are ordered to minimise crossings, then
        the distance of nets to their preferred lane index, and then the length of the
        conduits leaving the lanes sideways. Then nets move into the free parts of
        other lanes, including the pinned ones, as long as that doesn't add crossings
        (see _merge).

        Finally, empty lanes are added at the start if that moves nets to their
        preferred lane.

        Args:
            preferred: Preferred lane index per net, for nets that continue in another
                duct at the same height: they go straight if their index is the same.
            pinned: (net, lane index) for nets whose lane is fixed, in order of
                priority.
        """
        self._preferred = preferred or {}
        self._spans = {}
        pinned_groups: list[list[ConduitNet]] = []
        for net, index in pinned or []:
            while index < len(pinned_groups) and self._overlap(
                pinned_groups[index], [net]
            ):
                index += 1
            while len(pinned_groups) <= index:
                pinned_groups.append([])
            pinned_groups[index].append(net)
        pinned_nets = {net for group in pinned_groups for net in group}

        others = sorted(set(self._attachments) - pinned_nets, key=self._span)
        groups = [[net] for net in self._order(others)]
        groups = self._merge(pinned_groups + groups, len(pinned_groups))
        # Start with empty lanes (after the pinned ones) if all nets with a preferred
        # lane are before it
        shifts = [
            self._preferred[net] - i
            for i, group in enumerate(groups)
            for net in group
            if net in self._preferred
        ]
        if shifts and min(shifts) > 0:
            n = len(pinned_groups)
            groups = groups[:n] + [[] for _ in range(min(shifts))] + groups[n:]
        self.lanes = [Lane(self, i, group) for i, group in enumerate(groups)]
        self._lane_of = {net: lane for lane in self.lanes for net in lane.nets}

    def _merge(
        self, groups: list[list["ConduitNet"]], fixed: int
    ) -> list[list["ConduitNet"]]:
        """Move nets into other lanes where they fit, to use fewer lanes.

        The nets of a lane move into the first lane (from the start) that they don't
        overlap with, if that doesn't add crossings or move nets away from their
        preferred lane. The first `fixed` lanes stay where they are.
        """

        def cost(groups: list[list[ConduitNet]]) -> tuple[int, int]:
            misaligned = sum(
                abs(i - self._preferred[net])
                for i, group in enumerate(groups)
                for net in group
                if net in self._preferred
            )
            return self._total_crossings(groups), misaligned

        merged = True
        while merged:
            merged = False
            current = cost(groups)
            for j in range(fixed, len(groups)):
                for i in range(len(groups)):
                    if i == j or self._overlap(groups[i], groups[j]):
                        continue
                    candidate = [
                        group + groups[j] if k == i else group
                        for k, group in enumerate(groups)
                        if k != j
                    ]
                    if cost(candidate) <= current:
                        groups = candidate
                        merged = True
                        break
                if merged:
                    break
        return groups

    def _overlap(self, first: list["ConduitNet"], second: list["ConduitNet"]) -> bool:
        """Whether any net of `first` overlaps with any net of `second`."""
        for a, b in itertools.product(first, second):
            lo_a, hi_a = self._span(a)
            lo_b, hi_b = self._span(b)
            if lo_a <= hi_b and lo_b <= hi_a:
                return True
        return False

    def _crossings(self, first: list["ConduitNet"], second: list["ConduitNet"]) -> int:
        """Number of crossings between two lanes, when `first` is before `second`.

        A conduit leaving `second` to the `before` side crosses `first`, if `first` is
        occupied at that point (and vice versa). Conduits that touch count as crossing.
        """
        crossings = 0
        for a, b in itertools.product(first, second):
            lo_a, hi_a = self._span(a)
            lo_b, hi_b = self._span(b)
            for attachment in self._attachments[b]:
                key = self._key(b, attachment)
                if attachment.side == self.before and lo_a <= key <= hi_a:
                    crossings += 1
            for attachment in self._attachments[a]:
                key = self._key(a, attachment)
                if attachment.side == self.after and lo_b <= key <= hi_b:
                    crossings += 1
        return crossings

    def _total_crossings(self, groups: list[list["ConduitNet"]]) -> int:
        """Number of crossings with the nets in `groups` on consecutive lanes."""
        return sum(
            self._crossings(groups[i], groups[j])
            for i in range(len(groups))
            for j in range(i + 1, len(groups))
        )

    def _stub_balance(self, net: "ConduitNet") -> int:
        """How much a net wants to be at the end rather than at the start: the number
        of conduits leaving it to the `after` side minus those leaving to `before`."""
        sides = [attachment.side for attachment in self._attachments[net]]
        return sides.count(self.after) - sides.count(self.before)

    def _order(self, nets: list["ConduitNet"]) -> list["ConduitNet"]:
        """Order of nets (each on its own lane) with the lowest cost, see
        assign_lanes."""
        n = len(nets)
        crossings = [
            [self._crossings([nets[i]], [nets[j]]) if i != j else 0 for j in range(n)]
            for i in range(n)
        ]
        balance = [self._stub_balance(net) for net in nets]

        preferred = [self._preferred.get(net) for net in nets]

        def cost(order: tuple[int, ...] | list[int]) -> tuple[int, int, int]:
            n_crossings = sum(
                crossings[order[i]][order[j]] for i in range(n) for j in range(i + 1, n)
            )
            misaligned = sum(
                abs(i - preferred[k])
                for i, k in enumerate(order)
                if preferred[k] is not None
            )
            # A lane at position i has i lanes before it, and n - 1 - i after it
            stubs = sum(-balance[k] * i for i, k in enumerate(order))
            return n_crossings, misaligned, stubs

        if n <= 6:
            best = min(itertools.permutations(range(n)), key=cost)
            return [nets[k] for k in best]

        # Too many lanes to try all orders: swap neighbours while that improves. A
        # swap only changes the cost of the two lanes involved.
        def swap_delta(i: int, a: int, b: int) -> tuple[int, int, int]:
            """Change in cost when a (at position i) and b (at i + 1) swap."""

            def shift(k: int, old: int, new: int) -> int:
                p = preferred[k]
                return 0 if p is None else abs(new - p) - abs(old - p)

            return (
                crossings[b][a] - crossings[a][b],
                shift(a, i, i + 1) + shift(b, i + 1, i),
                balance[b] - balance[a],
            )

        order = list(range(n))
        improved = True
        while improved:
            improved = False
            for i in range(n - 1):
                if swap_delta(i, order[i], order[i + 1]) < (0, 0, 0):
                    order[i], order[i + 1] = order[i + 1], order[i]
                    improved = True
        return [nets[k] for k in order]


class ConduitDuct(Duct):
    """Column between two components of a timeline, with vertical lanes.

    Its x and y are in the frame of its timeline.
    """

    horizontal = False
    before = "left"
    after = "right"

    def __init__(self, timeline: "TimelineBlock") -> None:
        super().__init__(timeline)

    def calc_layout(self) -> None:
        """Set the width of this column, self.x must be set by the TimelineBlock.

        The height is set by the TimelineBlock, once its height is known.
        """
        self.width = self.size


class TopConduitDuct(Duct):
    """Row above a timeline, with horizontal lanes.

    The subtimelines of one component share a single TopConduitDuct: its x and y are
    in the frame of the timeline of that component. The TopConduitDuct of the root
    timeline is in the frame of the model.
    """

    horizontal = True
    before = "up"
    after = "down"

    @property
    def minimum_height(self) -> float:
        """The minimum height needed for all lanes."""
        return self.size
