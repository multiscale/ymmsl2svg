import logging

import svg
from ymmsl.v0_2 import Conduit, Model, Operator, TimelineTree

from ymmsl2svg.base import SvgBlock
from ymmsl2svg.settings import settings
from ymmsl2svg.timeline_block import TimelineBlock

logger = logging.getLogger(__name__)


class ModelBlock(SvgBlock):
    """SVG Block that represents a single Model, including its ports"""

    def __init__(self, model: Model) -> None:
        super().__init__()
        self.model = model

        self.ports = {}

        # Determine timelines
        self.timeline_tree = TimelineTree(self.model)
        self.timeline_tree.check_consistent()
        self.timeline_block = TimelineBlock(self.timeline_tree, self.timeline_tree.root)

        # Route conduits
        components = self.timeline_block.map_components()
        for conduit in self.model.conduits:
            sending_component = conduit.sending_component()
            receiving_component = conduit.receiving_component()
            components.get(sending_component, self).add_conduit(conduit)
            components.get(receiving_component, self).add_conduit(conduit)
        self.timeline_block.route_conduits()
        self.calc_layout()

    def add_conduit(self, conduit: Conduit) -> None:
        """Add a conduit going to / originating from a model port."""
        if len(conduit.sending_component()) == 0:
            port = self.model.ports[conduit.sending_port()]
        else:
            assert len(conduit.receiving_component()) == 0
            port = self.model.ports[conduit.receiving_port()]
        tcd = self.timeline_block.top_conduit_duct
        if port.operator is Operator.F_INIT:
            idx = tcd.add_virtual_port(conduit, left=True)
            self.ports[conduit.sending_port()] = idx
        elif port.operator is Operator.O_F:
            idx = tcd.add_virtual_port(conduit, left=False)
            self.ports[conduit.receiving_port()] = idx
        else:
            logger.warning(
                "Ignoring %s: visualization of %s ports on models is not implemented.",
                conduit,
                port.operator.name,
            )

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
        for portname, idx in self.ports.items():
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
