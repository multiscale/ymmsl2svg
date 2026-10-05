from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import svg
from ymmsl.v0_2 import (
    Component,
    Conduit,
    Identifier,
    Operator,
    Port,
    Reference,
    Timeline,
)

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings

if TYPE_CHECKING:
    from ymmsl2svg.conduit_ducts import ConduitDuct, Lane, Point
    from ymmsl2svg.timeline_block import TimelineBlock


def ports_for_operator(component: Component, operator: Operator) -> list[Port]:
    """Return a list of ports of the component for the given operator"""
    return [port for port in component.ports.values() if port.operator is operator]


class ComponentBlock(SvgBlock):
    """SVG Block that represents a single component in a model, including its ports."""

    def __init__(
        self,
        component: Component,
        subtimelines: list["TimelineBlock"],
        left_conduit_duct: "ConduitDuct",
        right_conduit_duct: "ConduitDuct",
        timeline: Timeline,
    ) -> None:
        super().__init__()
        # Geometry
        self.component_x: float = 0
        """x-position of the component rectangle"""
        self.component_width: float = 0
        """Width of the component rectangle"""
        self.component_height: float = 0
        """Height of the component rectangle"""

        # Data
        self.component = component
        self.subtimelines = subtimelines
        self.timeline = timeline
        """Absolute timeline this component itself lives on"""

        # Conduit connections to the left/right of this component
        self.left_conduit_duct = left_conduit_duct
        self.right_conduit_duct = right_conduit_duct
        left_conduit_duct.add_right_connector(self)
        right_conduit_duct.add_left_connector(self)
        # Conduit connections to subtimelines. Each subtimeline gets its own slot in
        # the same left/right ducts as this component.
        for subtl in subtimelines:
            left_conduit_duct.add_right_connector(subtl.top_conduit_duct)
            right_conduit_duct.add_left_connector(subtl.top_conduit_duct)
            subtl.top_conduit_duct.add_top_component(self)

        self._ports_per_operator = {
            op: ports_for_operator(component, op) for op in Operator
        }
        self.f_init_ports = self._ports_per_operator[Operator.F_INIT]
        self.o_f_ports = self._ports_per_operator[Operator.O_F]
        self.o_i_ports = self._ports_per_operator[Operator.O_I]
        self.s_ports = self._ports_per_operator[Operator.S]

        self.port_positions: dict[Identifier, tuple[float, float]] = {}
        self._port_segment_x: dict[Identifier, float] = {}
        """x-offset of the subtimeline that each O_I/S port belongs to, used to convert
        its position to be relative to that subtimeline."""
        self._subtimeline_y: float = 0
        """y-offset of the subtimelines (shared by all of them), used to convert an
        O_I/S port's position to be relative to its subtimeline."""
        self.conduits_per_port: dict[Identifier, list[Conduit]] = {
            port: [] for port in self.component.ports
        }

        # Deferred import to avoid a circular import with conduit_ducts.py.
        from ymmsl2svg.conduit_ducts import Lanes

        self._pass_lanes = Lanes(horizontal=True)
        """Shared horizontal lanes (one per sender) for conduits that pass over one or
        more of our subtimelines to reach another one. Drawn once, directly above all of
        our subtimelines, regardless of how many of them a given conduit crosses."""

    def _ports_iter(self, operator: Operator, timeline: Timeline | None, reverse: bool):
        """Iterate over the component's ports for the given operator.

        timeline is the absolute timeline of a subtimeline. Ports are matched by making
        their own (relative) .timeline absolute. If timeline is None or empty, all ports
        are returned unfiltered.
        """
        ports = self._ports_per_operator[operator]
        if timeline:
            ports = [port for port in ports if self._port_timeline(port) == timeline]
        if reverse:
            ports = reversed(ports)
        yield from ports

    def _port_timeline(self, port: Port) -> Timeline:
        """Absolute timeline of a port, following ymmsl's timeline_for_port: a port
        without annotation is on <parent_tl>:<component>, one annotated with "subtl" is
        on <parent_tl>:<component>.subtl."""
        name = self.component.name
        if port.timeline:
            return self.timeline + Timeline([f"{name}.{tl}" for tl in port.timeline])
        return self.timeline + Timeline([name])

    def add_conduit(self, conduit: Conduit):
        """Register conduit for this component."""
        if conduit.sending_component() == self.component.name:
            portname = conduit.sending_port()
        elif conduit.receiving_component() == self.component.name:
            portname = conduit.receiving_port()
        else:
            raise RuntimeError("Unreachable")
        self.conduits_per_port[portname].append(conduit)

    def conduits_per_operator(
        self,
        operator: Operator,
        timeline: Timeline | None = None,
        reversed: bool = False,
    ) -> Iterator[Conduit]:
        """Iterate over all conduits connected to ports of an operator.

        Args:
            operator: Operator to filter on.
            timeline: Timeline to filter on (only applicable to O_I and S ports).
            reversed: Reverse the order of the conduits.
        """
        for port in self._ports_iter(operator, timeline, reversed):
            yield from self.conduits_per_port.get(port.name, [])

    def ports_per_operator(
        self,
        operator: Operator,
        timeline: Timeline | None = None,
        reversed: bool = False,
    ) -> Iterator[Reference]:
        """Iterate over full port references of all ports of an operator.

        Args:
            operator: Operator to filter on.
            timeline: Timeline to filter on (only applicable to O_I and S ports).
            reversed: Reverse the order of the ports.
        """
        for port in self._ports_iter(operator, timeline, reversed):
            yield self.component.name + port.name

    def cmp_ports(self, port1: Identifier, port2: Identifier) -> int:
        """Comparison function for component ports"""
        op = self.component.ports[port1].operator
        for p in self._ports_per_operator[op]:
            if p.name == port1:
                return -1 if op is Operator.O_F else 1
            elif p.name == port2:
                return 1 if op is Operator.O_F else -1
        raise RuntimeError("Unreachable")

    def sort_output_ports(self, key: Callable) -> None:
        """Sort O_I/O_F ports in this component"""

        def port_sort(port: Port):
            return key(self.conduits_per_port[port.name])

        self.o_i_ports.sort(key=port_sort)
        self.o_f_ports.sort(key=port_sort, reverse=True)

    def sort_input_ports(self, key: Callable) -> None:
        """Sort F_INIT/S ports in this component"""

        def port_sort(port: Port):
            return key(self.conduits_per_port[port.name])

        self.f_init_ports.sort(key=port_sort)
        self.s_ports.sort(key=port_sort)

    def get_port_position(self, port: Identifier):
        """Get x,y position of the port.

        N.B. F_INIT and O_F ports will return the position w.r.t. the timeline that this
        component is in. O_I and S ports will return the position w.r.t. their
        subtimeline.
        """
        if self.component.ports[port].operator in (Operator.F_INIT, Operator.O_F):
            return self.port_positions[port]
        # Relative to subtimeline:
        x, y = self.port_positions[port]
        return (x - self._port_segment_x[port], y - self._subtimeline_y)

    def pass_lanes_to_duct(
        self, tlblock: "TimelineBlock", key: Reference, duct_on_left: bool
    ) -> list["Lane"]:
        """Lane needed if a conduit has to cross one or more sibling subtimelines to
        travel between `tlblock` and the ConduitDuct they all share, on its left
        (duct_on_left) or right. Conduits sharing the same `key` (usually the sender,
        but see ConduitDuct.get_conduits for exceptions) share a single lane for this,
        regardless of how many siblings they cross (see self._pass_lanes)."""
        idx = self.subtimelines.index(tlblock)
        crosses_any = idx > 0 if duct_on_left else idx < len(self.subtimelines) - 1
        if not crosses_any:
            return []
        return [self._pass_lanes[key]]

    def sibling_point_for(
        self, frame: "TimelineBlock", conduit: Conduit
    ) -> tuple[list["Lane"], "Point"] | None:
        """Hand `conduit` one hop closer to wherever it needs to go next, from `frame`
        (one of self.subtimelines) towards a sibling: return an empty lane list and a
        Point (in `frame`'s own coordinate frame) for a virtual port on the immediate
        neighbor of `frame` in that direction. That neighbor's own routing picks it up
        from there (as a conduit arriving from its parent), continuing the hand-off
        itself if it still isn't reachable there -- so a multi-subtimeline crossing is
        drawn as a chain of single hops, each sharing whatever lanes its own
        subtimeline already uses for that sender, rather than a lane bypassing the
        siblings in between.

        If the destination lives in a sibling, hop straight towards it. Otherwise
        `conduit` is simply leaving this timeline (conduits always exit to the right,
        see TopConduitDuct._route_to_sibling_or_parent): if `frame` isn't already the
        rightmost sibling, still hop one step right so the conduit passes through that
        sibling's own duct network on its way out, instead of skipping over it. None
        only once `frame` is the rightmost sibling -- there's nowhere closer left to
        hop to, so it must genuinely exit to our own parent duct.
        """
        from ymmsl2svg.conduit_ducts import SiblingVirtualPortPoint

        frame_idx = self.subtimelines.index(frame)
        target_idx = None
        for i, subtl in enumerate(self.subtimelines):
            if i != frame_idx and (
                conduit.receiving_component() in subtl.top_conduit_duct.destinations()
            ):
                target_idx = i
                break

        if target_idx is not None:
            next_idx = frame_idx + 1 if target_idx > frame_idx else frame_idx - 1
        elif frame_idx < len(self.subtimelines) - 1:
            next_idx = frame_idx + 1
        else:
            return None

        enter_from_left = next_idx > frame_idx
        tcd = self.subtimelines[next_idx].top_conduit_duct
        vidx = tcd.add_virtual_port(conduit, left=enter_from_left)
        point = SiblingVirtualPortPoint(frame, tcd, vidx, left=not enter_from_left)
        return [], point

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        subtimeline_width = sum(subtl.width for subtl in self.subtimelines)
        text_width = self.estimate_name_width() + 2 * settings.text_margin
        self.component_width = max(
            settings.component_width, subtimeline_width, text_width
        )
        # Make space for f_init and o_f ports
        self.width = self.component_width + 2 * settings.port_size
        self.component_x = self.x + settings.port_size

        max_num_ports = max(len(self.f_init_ports), len(self.o_f_ports))
        port_height = max_num_ports * settings.port_margin
        self.component_height = max(settings.component_height, port_height)
        self.height = self.component_height

        # Make space for o_i and s ports
        if self.o_i_ports or self.s_ports:
            self.height += settings.port_size
        # O_I/S ports sit right below the component, at the top of the pass-lanes gap
        # (added next) -- so this must be captured before that gap changes self.height.
        entry_y = self.y + self.height

        # Make space for lanes shared by conduits passing over one of our
        # subtimelines to reach another (see pass_lanes_to_duct), directly above all
        # of our subtimelines.
        if len(self._pass_lanes):
            lane_offset = entry_y + settings.hlane_margin / 2
            spacing = settings.hlane_margin
            self.height += self._pass_lanes.set_pos(lane_offset, spacing)
            self.height += settings.hlane_margin

        # Calculate (x, y) positions for each port
        for ports, x in [(self.f_init_ports, 0), (self.o_f_ports, self.width)]:
            y = self.component_height / 2 - (len(ports) - 1) * settings.port_margin / 2
            for port in ports:
                self.port_positions[port.name] = (self.x + x, self.y + y)
                y += settings.port_margin

        segment_bounds = self._segment_bounds()
        self._align_subtimelines()
        self._subtimeline_y = self.y + self.height
        for i, timeline in enumerate(self.subtimelines):
            self._place_subtimeline(i, timeline, segment_bounds, entry_y)

    def _segment_bounds(self) -> list[float]:
        """x-boundaries of each subtimeline's segment: segment i spans [bounds[i],
        bounds[i + 1]), packed left to right by each subtimeline's own width. Leftover
        width goes after the last segment."""
        bounds = [self.component_x]
        for timeline in self.subtimelines[:-1]:
            bounds.append(bounds[-1] + timeline.width)
        bounds.append(self.component_x + self.component_width)
        return bounds

    def _align_subtimelines(self) -> None:
        """Give every sibling subtimeline the tallest top_conduit_duct height needed by
        any one of them, so their components line up regardless of which subtimeline
        they're in."""
        max_top_height = max(
            (subtl.top_conduit_duct.height for subtl in self.subtimelines), default=0
        )
        for subtl in self.subtimelines:
            if subtl.top_conduit_duct.height < max_top_height:
                subtl.calc_layout(min_top_height=max_top_height)

    def _place_subtimeline(
        self,
        i: int,
        timeline: "TimelineBlock",
        segment_bounds: list[float],
        entry_y: float,
    ) -> None:
        """Position `timeline`'s O_I/S ports (at entry_y, right below the component)
        and move it into its segment."""
        seg_x, seg_right = segment_bounds[i], segment_bounds[i + 1]
        oi_ports = list(
            self._ports_iter(Operator.O_I, timeline.node.timeline, reverse=False)
        )
        s_ports = list(
            self._ports_iter(Operator.S, timeline.node.timeline, reverse=False)
        )

        oi_offset, s_offset = timeline.top_conduit_duct.port_offsets(self)
        x0 = seg_x + oi_offset
        for j, port in enumerate(oi_ports):
            x = x0 + (j + 0.5) * settings.port_margin
            self.port_positions[port.name] = (x, entry_y)
            self._port_segment_x[port.name] = seg_x

        x0 = seg_right + s_offset
        for j, port in enumerate(s_ports):
            x = x0 + (j + 0.5) * settings.port_margin
            self.port_positions[port.name] = (x, entry_y)
            self._port_segment_x[port.name] = seg_x

        # Move subtimeline
        timeline.moveto(seg_x, self._subtimeline_y)

    def estimate_name_width(self) -> float:
        """Estimate the width (in pixels) of the component's name."""
        # This is intended to slightly overestimate the width (which is prettier than
        # underestimation). Note that the exact size depends on the font used by the SVG
        # viewer, so better estimates (e.g. using pillow to calculate the bounding box)
        # may still be wrong.

        # See `scripts/textsize.py` for character sizes. Assumed font size is 16px.
        # Narrow characters: (3-6 pixels) -> estimate as 5 pixels wide
        # Broad characters: (11+ pixels) -> estimate as 13 pixels wide
        # Medium characters: (8-10 pixels) -> estimate as 10 pixels wide
        lengths = {c: 5 for c in "ijlrIft.[]"} | {c: 13 for c in "BCPRUDGHNOQwmMW"}
        return sum(lengths.get(c, 10) for c in str(self.component.name))

    def to_svg(self) -> svg.G:
        """Build the SVG representing this component and its ports."""
        group = super().to_svg()
        assert group.elements is not None
        component = svg.Rect(
            x=self.component_x + settings.component_border / 2,
            y=self.y + settings.component_border / 2,
            width=self.component_width - settings.component_border,
            height=self.component_height - settings.component_border,
            class_=["component"],
            id=f"component-{self.component.name}",
        )
        text = svg.Text(
            text=str(self.component.name),
            x=self.component_x + self.component_width / 2,
            y=self.y + self.component_height / 2,
        )
        group.elements.extend([component, text])

        # Draw ports
        for ports, use_id in [
            (self.f_init_ports, "#port-f_init"),
            (self.o_f_ports, "#port-o_f"),
            (self.o_i_ports, "#port-o_i"),
            (self.s_ports, "#port-s"),
        ]:
            for port in ports:
                x, y = self.port_positions[port.name]
                title = svg.Title(text=str(port.name))
                use = svg.Use(href=use_id, x=x, y=y, elements=[title])
                group.elements.append(use)

        return group
