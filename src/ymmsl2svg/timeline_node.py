from ymmsl.v0_2 import Component, Model, Reference, Timeline, resolve_timelines


def create_timeline_tree(model: Model) -> "TimelineNode":
    """Determine the timeline tree for a model."""
    resolve_timelines(model)
    root = TimelineNode(Timeline(":"), None)

    for component in model.components.values():
        assert component.timeline is not None
        node = root[component.timeline]
        node.add_component(component)

    return root


class TimelineNode:
    def __init__(self, timeline: Timeline, parent: "TimelineNode | None") -> None:
        self.timeline = timeline
        """Timeline for this node."""
        self.parent = parent
        """Parent node of this node."""

        self.children: dict[Reference, TimelineNode] = {}
        """Child timeline nodes."""
        self.components: list[Component] = []
        """Components that are part of this timeline."""
        self.parent_components: list[Component] = []
        """Parent components, i.e. those with O_I or S ports that send/recieve in
        this timeline."""

    def __getitem__(self, timeline: Timeline) -> "TimelineNode":
        """Get a sub-timeline of this one, creating a new one if required."""
        assert not timeline.absolute or self.parent is None
        node = self
        for part in timeline:
            if part not in node.children:
                subtl = node.timeline + Timeline(str(part))
                node.children[part] = TimelineNode(subtl, node)
            node = node.children[part]
        return node

    def add_component(self, component: Component) -> None:
        """Register a component with this timeline node"""
        self.components.append(component)
        for port in component.ports.values():
            if port.timeline:  # Only take O_I and S ports with a (sub)timeline
                node = self[port.timeline]
                if component not in node.parent_components:
                    node.parent_components.append(component)
