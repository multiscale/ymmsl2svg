import logging
import sys
from functools import cmp_to_key

import svg
from ymmsl.v0_2 import Conduit, Identifier, Model, Operator, TimelineTree

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings
from ymmsl2svg.timeline_block import TimelineBlock

logger = logging.getLogger(__name__)


class ModelBlock(SvgBlock):
    """SVG Block that represents a single Model, including its ports"""

    def __init__(self, model: Model) -> None:
        super().__init__()
        self.model = model

        self._ports_per_operator = {
            op: [port for port in self.model.ports.values() if port.operator is op]
            for op in Operator
        }
        self.f_init_ports = self._ports_per_operator[Operator.F_INIT]
        self.o_f_ports = self._ports_per_operator[Operator.O_F]
        self.o_i_ports = self._ports_per_operator[Operator.O_I]
        self.s_ports = self._ports_per_operator[Operator.S]
        self.port_indices: dict[Identifier, int] = {}
        self.conduits_per_port: dict[Identifier, list[Conduit]] = {
            port: [] for port in self.model.ports
        }

        # Determine timelines
        self.timeline_tree = TimelineTree(self.model)
        self.timeline_tree.check_consistent()

        # Create graph components
        self.timeline_block = TimelineBlock(self.timeline_tree, self.timeline_tree.root)

        # Route conduits
        self.components = self.timeline_block.map_components()
        for conduit in self.model.conduits:
            sending_component = conduit.sending_component()
            receiving_component = conduit.receiving_component()
            self.components.get(sending_component, self).add_conduit(conduit)
            if sending_component != receiving_component:
                self.components.get(receiving_component, self).add_conduit(conduit)
        self.sort_ports_and_conduits()
        self.timeline_block.route_conduits()
        self.calc_layout()

        # Get indices of our O_F ports so we can draw them in to_svg()
        tcd = self.timeline_block.top_conduit_duct
        for port in self.o_f_ports:
            for conduit in self.conduits_per_port[port.name]:
                self.port_indices[port.name] = tcd.add_virtual_port(conduit, left=False)
        # We currently don't support drawing model S or O_I ports:
        if self.s_ports or self.o_i_ports:
            logger.warning(
                "Visualization of S ports and O_I ports on the model is not "
                "implemented. These ports will not be visible, and conduits from and "
                "to these conduits may be drawn incorrectly or not at all."
            )

    def add_conduit(self, conduit: Conduit) -> None:
        """Add a conduit going to / originating from a model port."""
        if len(conduit.sending_component()) == 0:
            portname = conduit.sending_port()
            self.conduits_per_port[portname].append(conduit)
        if len(conduit.receiving_component()) == 0:
            portname = conduit.receiving_port()
            self.conduits_per_port[portname].append(conduit)

    def sort_ports_and_conduits(self) -> None:
        """Sort ports for all components.

        Note: this function also registers conduits coming from model ports, which needs
        to happen after sorting input ports and before sorting the output ports
        (otherwise self.cmp_ports will not work).
        """
        if settings.resequence_ports:
            # Determine the sort order for all components
            self.component_sort_keys = self.timeline_block.get_component_sort_keys()
            # Sort key for output ports
            output_sort_key = cmp_to_key(self._output_cmp)
            # Sort F_INIT ports
            self.f_init_ports.sort(
                key=lambda port: output_sort_key(self.conduits_per_port[port.name]),
                reverse=True,
            )

        # Reserve space in the top conduit duct for our (now-sorted) F_INIT ports:
        tcd = self.timeline_block.top_conduit_duct
        for port in self.f_init_ports:
            for conduit in self.conduits_per_port[port.name]:
                self.port_indices[port.name] = tcd.add_virtual_port(conduit, left=True)

        if settings.resequence_ports:
            # Sort output ports of all components:
            self.timeline_block.sort_output_ports(output_sort_key)
            # And then input ports
            self.timeline_block.sort_input_ports(cmp_to_key(self._input_cmp))
            # N.B. Model O_F ports are sorted by default by the conduit routing algo.

    def _sorted_component_keys(self, conduits: list[Conduit]) -> list[tuple[int, ...]]:
        """Return sorted component keys of the conduit receivers."""
        return sorted(
            self.component_sort_keys.get(conduit.receiving_component(), ())
            + (sys.maxsize,)  # Sort subtimelines of a component before the component
            for conduit in conduits
        )

    def _output_cmp(self, conduits1: list[Conduit], conduits2: list[Conduit]) -> int:
        """Comparison function for sorting O_I and O_F ports."""
        # NOTE: O_I is sorted left->right, O_F bottom->top
        # Sort disconnected ports first
        if not conduits1:
            return 0 if not conduits2 else -1
        if not conduits2:
            return 1
        # Check connected conduits
        component1_keys = self._sorted_component_keys(conduits1)
        component2_keys = self._sorted_component_keys(conduits2)
        for k1, k2 in zip(component1_keys, component2_keys, strict=False):
            if k1 == k2:
                continue
            return -1 if k1 < k2 else 1
        return -1 if len(conduits1) < len(conduits2) else 1

    def _input_cmp(self, conduits1: list[Conduit], conduits2: list[Conduit]) -> int:
        """Comparison function for sorting F_INIT and S ports."""
        # NOTE: F_INIT is sorted top->bottom, S left->right
        # Disconnected ports are last
        if not conduits1:
            return 0 if not conduits2 else 1
        if not conduits2:
            return -1
        # Input ports may have only one conduit:
        assert len(conduits1) == len(conduits2) == 1
        component1 = self.component_sort_keys.get(conduits1[0].sending_component(), ())
        component2 = self.component_sort_keys.get(conduits2[0].sending_component(), ())
        if component1 == component2:
            comp = self.components.get(conduits1[0].sending_component(), self)
            port1 = conduits1[0].sending_port()
            port2 = conduits2[0].sending_port()
            return comp.cmp_ports(port1, port2)
        destination = self.component_sort_keys[conduits1[0].receiving_component()]
        parent = destination[:-1]
        # Conduits coming from our parent component are always first
        if component1 == parent:
            return -1
        if component2 == parent:
            return 1
        if (component1 <= parent) is (component2 <= parent):
            # Both are from a parent timeline, or both are from this timeline
            return -1 if component1 < component2 else 1
        # The port connected to the earlier timeline sould be earlier
        return -1 if component1 <= parent else 1

    def cmp_ports(self, port1: Identifier, port2: Identifier) -> int:
        """Comparison function for model ports"""
        return -1 if self.port_indices[port1] < self.port_indices[port2] else 1

    def calc_layout(self) -> None:
        """Calculate layout of all internal components"""
        self.timeline_block.calc_layout()
        self.width = self.timeline_block.width + 4 * settings.port_margin
        self.height = self.timeline_block.height + 4 * settings.port_margin
        self.timeline_block.moveto(2 * settings.port_margin, 2 * settings.port_margin)

    def to_svg(self) -> svg.G:
        pm = settings.port_margin
        group = super().to_svg()
        assert group.elements is not None
        model_block = svg.Rect(
            x=pm + settings.model_border / 2,
            y=pm + settings.model_border / 2,
            width=self.width - 2 * pm - settings.model_border,
            height=self.height - 2 * pm - settings.model_border,
            class_=["model"],
            id=f"model-{self.model.name}",
        )
        group.elements.append(model_block)
        # Draw ports
        for portname, idx in self.port_indices.items():
            port = self.model.ports[portname]
            title = svg.Title(text=str(port.name))
            if port.operator == Operator.F_INIT:
                useid = "#port-f_init"
                x = pm - settings.port_size
                y = (2.5 + idx) * pm
                path: list[svg.PathData] = [svg.M(pm, y), svg.h(pm)]
            elif port.operator == Operator.O_F:
                useid = "#port-o_f"
                x = self.width - pm + settings.port_size
                y = (2.5 + idx) * pm
                path: list[svg.PathData] = [svg.M(self.width - pm, y), svg.h(-pm)]
            use = svg.Use(href=useid, x=x, y=y, elements=[title])
            group.elements.append(use)
            group.elements.append(svg.Path(d=path, class_=["conduit"]))
        group.elements.append(self.timeline_block.to_svg())
        return group
