"""Routing of conduits over the grid of ducts (see conduit_ducts).

Routing happens in three steps:

1. Path: for each conduit, find the sequence of ducts with the fewest bends from its
   sending to its receiving port (_find_path). The ducts form a graph: a column
   connects to the row above its timeline, and to the rows below its neighbouring
   components; a row connects to the columns below it, and to the columns at its ends.
2. Lanes: each duct assigns a lane to each net passing through it (see
   Duct.assign_lanes).
3. Drawing: a conduit goes along its lane in each duct, so its path alternates between
   the x of a column lane and the y of a row lane, and only bends between two ducts
   (ConduitRoute.to_svg).
"""

import heapq
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import Conduit, Operator, Reference

from ymmsl2svg.conduit_ducts import (
    Attachment,
    ConduitDuct,
    Duct,
    Side,
    TopConduitDuct,
)
from ymmsl2svg.port_groups import HorizontalPortGroup, PortBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.model_block import ModelBlock
    from ymmsl2svg.timeline_block import TimelineBlock

COLUMN_TOP = -1e6
"""Attachment key of the top end of a column (where it meets the row above)."""
COLUMN_ROW_BELOW = 1e6
"""Attachment key in a column of the row below a neighbouring component."""
COLUMN_LEVEL = 2e6
"""Difference between the attachment keys in a column of the ports of components one
timeline level apart (see _side_port_ends)."""


class ConduitNet:
    """All conduits leaving one sending port, drawn as one line that branches."""

    def __init__(self, sender: Reference) -> None:
        self.sender = sender
        """Sending port of the conduits."""


@dataclass
class Step:
    """Part of a route that goes through one duct."""

    duct: Duct
    entry: Attachment
    """Where the route enters the duct."""
    exit: Attachment
    """Where the route leaves the duct."""


@dataclass
class ConduitRoute:
    """Route of a single conduit over the grid."""

    conduit: Conduit
    net: ConduitNet
    """Net of the conduit, which determines its lane in each duct."""
    steps: list[Step]
    """Ducts the conduit goes through, from sender to receiver."""

    def to_svg(self, start: tuple[float, float], end: tuple[float, float]) -> svg.Path:
        """Draw the route from the sending port at `start` to the receiving port at
        `end`."""
        x, y = start
        path: list[svg.PathData] = [svg.M(x, y)]

        def move(new_x: float, new_y: float) -> None:
            # Skip zero-length moves: with square line caps they would show as a stub.
            # Merge moves in the same direction.
            nonlocal x, y
            if new_x != x:
                if isinstance(path[-1], svg.H):
                    path.pop()
                path.append(svg.H(new_x))
            if new_y != y:
                if isinstance(path[-1], svg.V):
                    path.pop()
                path.append(svg.V(new_y))
            x, y = new_x, new_y

        # Move along the lane of the previous duct to the lane of the next one
        for step in self.steps:
            pos = step.duct.lane_of(self.net).pos
            if step.duct.horizontal:
                move(x, pos)
            else:
                move(pos, y)
        if self.steps[-1].duct.horizontal:
            move(end[0], y)
        else:
            move(x, end[1])
        move(*end)
        return svg.Path(d=path, class_=["conduit"])


class ConduitRouter:
    """Routes all conduits of a model over the grid of ducts."""

    def __init__(self, model_block: "ModelBlock") -> None:
        self.model_block = model_block
        root = model_block.timeline_block

        self._order: dict[ConduitDuct, int] = {}
        """Order of all columns from left to right, used as attachment key in rows."""
        self._depth: dict[Duct, int] = {model_block.top_conduit_duct: 0}
        """Nesting depth of the timeline of each column, or below each row."""
        self._number_columns(root, itertools.count(), 0)
        self._root_ends = (-1, len(self._order))
        """Attachment keys of the left and right end of the root row."""

        self._links: dict[Duct, list[tuple[Attachment, Duct, Attachment]]] = {}
        """Per duct: (exit, neighbouring duct, entry into the neighbour)."""
        self._components_below: dict[TopConduitDuct, set[Reference]] = {}
        """Per row below a component: that component and all components below it."""
        self._left_column_order: dict[TopConduitDuct, int] = {}
        """Per row below a component: order of the column left of that component."""
        self._add_links(root)

        self.routes: list[ConduitRoute] = []
        nets: dict[Reference, ConduitNet] = {}
        self._model_port_nets: dict[Reference, ConduitNet] = {}
        """Net of the conduits of each connected model port."""
        for conduit in model_block.model.conduits:
            starts = self._ends(conduit, sending=True)
            targets = dict(self._ends(conduit, sending=False))
            if not starts or not targets:
                continue  # Model S or O_I port, not supported
            net = nets.setdefault(conduit.sender, ConduitNet(conduit.sender))
            for sending in (True, False):
                if self._port(conduit, sending) is None:
                    name = (
                        conduit.sending_port() if sending else conduit.receiving_port()
                    )
                    self._model_port_nets.setdefault(name, net)
            steps = self._find_path(conduit, starts, targets)
            for step in steps:
                step.duct.attach(net, step.entry)
                step.duct.attach(net, step.exit)
            self.routes.append(ConduitRoute(conduit, net, steps))

        # The order of the lanes in a row depends on the columns below it, and that of
        # a column on the rows below its neighbouring components (see
        # Attachment.via). So assign lanes from the deepest timelines up, and for
        # each timeline first in its columns, then in the row above it.
        # Rows at the same depth are assigned from left to right, so a net crossing a
        # column from one row into the next can prefer the lane it has in the first.
        ducts = {step.duct for route in self.routes for step in route.steps}
        self.ducts = sorted(ducts, key=self._assignment_order)
        """All ducts that conduits go through."""
        neighbours = self._neighbouring_rows()
        for duct in self.ducts:
            preferred = {
                net: other.lane_of(net).index
                for net, other in neighbours.get(duct, [])
                if other.lanes
            }
            pinned = self._model_port_lanes() if duct is self._root_row else []
            duct.assign_lanes(preferred, pinned)
        self._align_ports()

    @property
    def _root_row(self) -> TopConduitDuct:
        return self.model_block.top_conduit_duct

    def _model_port_lanes(self) -> list[tuple[ConduitNet, int]]:
        """Lanes of the root row to pin the nets of the model ports to: the k-th
        F_INIT and the k-th O_F port of the model prefer lane k (see
        _model_port_ends)."""
        pinned = []
        for operator in (Operator.F_INIT, Operator.O_F):
            for index, name in enumerate(self.model_block.stacked_ports(operator)):
                net = self._model_port_nets.get(name)
                if net is not None and net not in {n for n, _ in pinned}:
                    pinned.append((net, index))
        return pinned

    def _assignment_order(self, duct: Duct) -> tuple[int, bool, int]:
        """Sort key for the order of assigning lanes (see __init__): deepest first,
        columns before rows, then from left to right."""
        if isinstance(duct, ConduitDuct):
            x = self._order[duct]
        else:
            x = self._left_column_order.get(duct, -1)  # type: ignore[arg-type]
        return -self._depth[duct], duct.horizontal, x

    def _neighbouring_rows(self) -> dict[Duct, list[tuple[ConduitNet, Duct]]]:
        """Per row: the nets that cross a column into another row at the same depth,
        and that other row."""
        neighbours: dict[Duct, list[tuple[ConduitNet, Duct]]] = {}
        for route in self.routes:
            for a, _, c in zip(route.steps, route.steps[1:], route.steps[2:]):
                if (
                    a.duct.horizontal
                    and c.duct.horizontal
                    and self._depth[a.duct] == self._depth[c.duct]
                ):
                    neighbours.setdefault(a.duct, []).append((route.net, c.duct))
                    neighbours.setdefault(c.duct, []).append((route.net, a.duct))
        return neighbours

    def _number_columns(self, timeline: "TimelineBlock", counter, depth: int) -> None:
        """Number the columns of a timeline and its subtimelines from left to right,
        and register the depth of all ducts."""
        for i, column in enumerate(timeline.conduit_ducts):
            self._order[column] = next(counter)
            self._depth[column] = depth
            if i < len(timeline.components):
                component = timeline.components[i]
                self._depth[component.subtimeline_duct] = depth + 1
                for subtimeline in component.subtimelines:
                    self._number_columns(subtimeline, counter, depth + 1)

    def _link(self, a: Duct, exit_a: Attachment, b: Duct, entry_b: Attachment) -> None:
        """Connect two ducts, in both directions."""
        self._links.setdefault(a, []).append((exit_a, b, entry_b))
        self._links.setdefault(b, []).append((entry_b, a, exit_a))

    def _add_links(self, timeline: "TimelineBlock") -> None:
        """Connect the ducts of a timeline and its subtimelines."""
        row = timeline.top_conduit_duct
        for column in timeline.conduit_ducts:
            self._link(
                column,
                Attachment(COLUMN_TOP, None),
                row,
                Attachment(self._order[column], "down", column),
            )
        for i, component in enumerate(timeline.components):
            if not component.subtimelines:
                continue
            row_below = component.subtimeline_duct
            left, right = timeline.conduit_ducts[i], timeline.conduit_ducts[i + 1]
            self._link(
                row_below,
                Attachment(self._order[left], None),
                left,
                Attachment(COLUMN_ROW_BELOW, "right", row_below),
            )
            self._link(
                row_below,
                Attachment(self._order[right], None),
                right,
                Attachment(COLUMN_ROW_BELOW, "left", row_below),
            )
            below = {component.component.name}
            for subtimeline in component.subtimelines:
                below.update(subtimeline.map_components())
                self._add_links(subtimeline)
            self._components_below[row_below] = below
            self._left_column_order[row_below] = self._order[left]

    def _port(self, conduit: Conduit, sending: bool) -> PortBlock | None:
        """The sending or receiving port of a conduit, None for a model port."""
        if sending:
            component, port = conduit.sending_component(), conduit.sending_port()
        else:
            component, port = conduit.receiving_component(), conduit.receiving_port()
        if not component:
            return None
        return self.model_block.components[component].ports[port]

    def _ends(self, conduit: Conduit, sending: bool) -> list[tuple[Duct, Attachment]]:
        """Ducts (and the attachment in it) that a conduit can start or end in."""
        port = self._port(conduit, sending)
        if port is None:
            return self._model_port_ends(conduit, sending)

        component = port.component
        if port.operator in (Operator.F_INIT, Operator.O_F):
            return self._side_port_ends(port)

        # O_I and S ports connect to the row below the component, or straight to the
        # first (O_I) or last (S) column of their subtimeline
        assert isinstance(port.group, HorizontalPortGroup)
        subtimeline = port.group.subtimeline
        if port.operator is Operator.O_I:
            column = subtimeline.conduit_ducts[0]
            key = self._order[column] + 0.5
        else:
            column = subtimeline.conduit_ducts[-1]
            key = self._order[column] - 0.5
        return [
            (column, Attachment(COLUMN_TOP, None)),
            (component.subtimeline_duct, Attachment(key, "up")),
        ]

    def _side_port_ends(self, port: PortBlock) -> list[tuple[Duct, Attachment]]:
        """Columns that an F_INIT or O_F port connects to.

        That is the column next to the port. If the component is the first (F_INIT)
        or last (O_F) one of its timeline, and that timeline is the first or last
        subtimeline of its parent component, nothing is in between the port and the
        column next to the parent component either: the port also connects straight
        across to that column, at the height of the port. And so on, up the timelines.
        """
        f_init = port.operator is Operator.F_INIT
        side: Side = "right" if f_init else "left"
        ports = port.group.ports(port.operator)
        key = ports.index(port) - (len(ports) - 1) / 2
        component = port.component
        ends: list[tuple[Duct, Attachment]] = []
        while True:
            column = (
                component.left_conduit_duct if f_init else component.right_conduit_duct
            )
            ends.append((column, Attachment(key, side)))
            timeline, parent = component.timeline, component.timeline.parent
            if parent is None:
                return ends
            first_or_last = 0 if f_init else -1
            if (
                timeline.components[first_or_last] is not component
                or parent.subtimelines[first_or_last] is not timeline
            ):
                return ends
            # The port is one timeline level deeper relative to the parent's column
            component = parent
            key += COLUMN_LEVEL

    def _model_port_ends(
        self, conduit: Conduit, sending: bool
    ) -> list[tuple[Duct, Attachment]]:
        """Ducts that a conduit from or to a model port can start or end in.

        Model ports are the left (F_INIT) or right (O_F) ends of the top lanes of the
        root row: they are stacked in the top corners of the model, each at the height
        of its lane (see _model_port_lanes), so their conduits enter the row straight.
        """
        name = conduit.sending_port() if sending else conduit.receiving_port()
        operator = self.model_block.model.ports[name].operator
        if operator not in (Operator.F_INIT, Operator.O_F):
            return []  # Model S and O_I ports are not supported
        end = self._root_ends[0 if operator is Operator.F_INIT else 1]
        return [(self._root_row, Attachment(end, None))]

    def _allowed(self, duct: Duct, conduit: Conduit) -> bool:
        """Whether a conduit may use a duct.

        The row below a component is only used by conduits from or to that component
        or a component below it, other conduits go over the component.
        """
        below = self._components_below.get(duct)  # type: ignore[arg-type]
        if below is None:
            return True
        return (
            conduit.sending_component() in below
            or conduit.receiving_component() in below
        )

    @staticmethod
    def _travel(duct: Duct, entry: Attachment, exit: Attachment) -> float:
        """Horizontal distance travelled through a duct."""
        return abs(exit.key - entry.key) if duct.horizontal else 0

    def _find_path(
        self,
        conduit: Conduit,
        starts: list[tuple[Duct, Attachment]],
        targets: dict[Duct, Attachment],
    ) -> list[Step]:
        """Find the path with the fewest bends (then the shortest horizontally) from
        one of the `starts` to one of the `targets` (Dijkstra's algorithm)."""
        counter = itertools.count()  # Tie breaker, so ducts are never compared
        # Heap items: (bends, travel, counter, duct, entry, previous). previous is
        # (previous item, exit from its duct). duct is None for a complete path.
        heap: list = []
        for duct, entry in starts:
            if self._allowed(duct, conduit):
                heapq.heappush(heap, (1, 0.0, next(counter), duct, entry, None))
        done: set[tuple[Duct, Attachment]] = set()
        while heap:
            item = heapq.heappop(heap)
            bends, travel, _, duct, entry, _ = item
            if duct is None:
                return self._unwind(item)
            if (duct, entry) in done:
                continue
            done.add((duct, entry))
            if duct in targets:
                exit = targets[duct]
                travel_end = travel + self._travel(duct, entry, exit)
                heapq.heappush(
                    heap, (bends, travel_end, next(counter), None, None, (item, exit))
                )
            for exit, neighbour, neighbour_entry in self._links.get(duct, []):
                if self._allowed(neighbour, conduit):
                    heapq.heappush(
                        heap,
                        (
                            bends + 1,
                            travel + self._travel(duct, entry, exit),
                            next(counter),
                            neighbour,
                            neighbour_entry,
                            (item, exit),
                        ),
                    )
        raise RuntimeError(f"Could not find a route for conduit {conduit}")

    @staticmethod
    def _unwind(item: tuple) -> list[Step]:
        """Get the steps of a complete path found by _find_path."""
        steps = []
        previous = item[5]
        while previous is not None:
            item, exit = previous
            steps.append(Step(item[3], item[4], exit))
            previous = item[5]
        return steps[::-1]

    def _align_ports(self) -> None:
        """Put O_I and S ports above the lane of the column that they connect straight
        to (see HorizontalPortGroup.calc_layout)."""
        for route in self.routes:
            for sending, step in [(True, route.steps[0]), (False, route.steps[-1])]:
                port = self._port(route.conduit, sending)
                if (
                    port is not None
                    and port.operator in (Operator.O_I, Operator.S)
                    and not step.duct.horizontal
                ):
                    port.lane = step.duct.lane_of(route.net)

    def _endpoint(self, route: ConduitRoute, sending: bool) -> tuple[float, float]:
        """Position of the sending or receiving port of a route."""
        port = self._port(route.conduit, sending)
        if port is not None:
            return port.absolute_position
        conduit = route.conduit
        name = conduit.sending_port() if sending else conduit.receiving_port()
        return self.model_port_position(name)

    def model_port_position(self, name: Reference) -> tuple[float, float]:
        """Position where conduits connect to a connected F_INIT or O_F model port:
        on the left or right edge of the model, at the height of its lane in the root
        row."""
        y = self._root_row.lane_of(self._model_port_nets[name]).pos
        pm = settings.port_margin
        f_init = self.model_block.model.ports[name].operator is Operator.F_INIT
        return (pm if f_init else self.model_block.width - pm), y

    def to_svg(self) -> svg.G:
        """Draw all conduits, in the frame of the model."""
        return svg.G(
            class_=["conduits"],
            elements=[
                route.to_svg(self._endpoint(route, True), self._endpoint(route, False))
                for route in self.routes
            ],
        )
