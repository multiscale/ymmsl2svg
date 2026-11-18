from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ymmsl2svg.timeline_block import TimelineBlock


class TimelineLayer:
    """Group of components in the same visual "layer" (which corresponds to the
    nested-ness of the timeline of the component).
    """

    def __init__(self) -> None:
        self._next_layer: TimelineLayer | None = None
        self.timeline_blocks: list[TimelineBlock] = []

        self.conduitduct_height: float = 0
        self.component_height: float = 0

    def next_layer(self) -> "TimelineLayer":
        """Get (and create as needed) the TimelineLayer below this one."""
        if self._next_layer is None:
            self._next_layer = TimelineLayer()
        return self._next_layer

    def add(self, timeline_block: "TimelineBlock") -> None:
        """Register a timeline block with this layer."""
        self.timeline_blocks.append(timeline_block)

    def calc_layout(self) -> None:
        """Calculate the layout of components within this timeline layer."""
        if self._next_layer is not None:
            self._next_layer.calc_layout()

        self.conduitduct_height = max(
            (tlb.top_conduit_duct.minimum_height for tlb in self.timeline_blocks),
            default=0,
        )
        self.component_height = max(
            (c.minimum_height for tlb in self.timeline_blocks for c in tlb.components),
            default=0,
        )

        for timeline_block in self.timeline_blocks:
            timeline_block.calc_layout()
