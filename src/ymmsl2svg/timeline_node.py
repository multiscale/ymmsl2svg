import heapq

from ymmsl.v0_2 import (
    Component,
    Model,
    Operator,
    Reference,
    Timeline,
    resolve_timelines,
)


def create_timeline_tree(model: Model) -> "TimelineNode":
    """Determine the timeline tree for a model."""
    resolve_timelines(model)
    root = TimelineNode(Timeline(":"), None)

    for component in model.components.values():
        assert component.timeline is not None
        node = root[component.timeline]
        node.add_component(component)

    root.calculate_component_order(model)
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

    def calculate_component_order(self, model: Model) -> None:
        """Determine order of components in this timeline."""
        # Group all components with shared timelines
        component_groups = self._group_components()
        group_per_component = {}
        for i, group in enumerate(component_groups):
            for component in group:
                group_per_component[component] = i

        # Find dependencies between the groups
        dependencies = self._finit_dependencies(model)
        group_deps: set[tuple[int, int]] = set()
        for sender, receiver in dependencies:
            # Check that we don't have dependencies between components in the same group
            if group_per_component[sender] is group_per_component[receiver]:
                raise RuntimeError(
                    "Unsupported coupling graph: found an F_INIT connection between "
                    f"'{sender}' and '{receiver}', who also share a child timeline."
                )
            group_deps.add((group_per_component[sender], group_per_component[receiver]))

        # Sequence components
        new_components = []
        for idx in self._topological_sort(list(group_deps), len(component_groups)):
            # Component sequence within a group is determined in _group_components()
            new_components.extend(component_groups[idx])
        assert set(new_components) == set(self.components)
        self.components = new_components

        # Repeat for each subtimeline
        for subtl in self.children.values():
            subtl.calculate_component_order(model)

    def _group_components(self) -> list[list[Component]]:
        """Group all components that (indirectly) share a timeline.

        For example, a timeline bridge connecting two components A and B will have two
        subtimelines: ":A" shared between components A and bridge, and ":B" shared
        between components B and bridge. All three components are part of a single
        group.
        """
        shared_subtl_per_component: dict[Component, list[TimelineNode]] = {}
        for subtl in self.children.values():
            if len(subtl.parent_components) > 2:
                raise RuntimeError(
                    f"Unsupported coupling graph: subtimeline '{subtl.timeline}' "
                    f"has {len(subtl.parent_components)} parent components, but we "
                    "support no more than 2."
                )
            if len(subtl.parent_components) > 1:
                for component in subtl.parent_components:
                    shared_subtl_per_component.setdefault(component, []).append(subtl)

        for comp, timelines in shared_subtl_per_component.items():
            if len(timelines) > 2:
                names = ", ".join(str(tl.timeline) for tl in timelines)
                raise RuntimeError(
                    f"Unsupported coupling graph: component '{comp.name}' has "
                    f"{len(timelines)} shared subtimelines ({names}), "
                    "but we support no more than 2."
                )

        component_groups = []
        done = set()
        for component in self.components:
            if component in done:
                continue
            if len(shared_subtl_per_component.get(component, [])) > 1:
                # We'll get back to this component later, the first component in the
                # group should be one with only a single shared timeline
                continue

            group = []
            next_component = component
            while next_component is not None:
                group.append(next_component)
                component = next_component
                next_component = None
                for subtl in shared_subtl_per_component.get(component, []):
                    for comp in subtl.parent_components:
                        if comp not in group:
                            assert next_component is None
                            next_component = comp
            component_groups.append(group)
            done.update(group)

        # Sanity check
        if len(done) != len(self.components):
            raise RuntimeError(
                "Could not assign components into groups, this may be a bug or an "
                "unsupported coupling graph."
            )
        return component_groups

    def _finit_dependencies(self, model: Model) -> list[tuple[Component, Component]]:
        """Determine dependencies resulting from conduits connected to F_INIT ports.

        Returns a list of component pairs, where the second component depends on a
        message of the first component.
        """
        dependencies: list[tuple[Component, Component]] = []
        # Determine dependencies between component groups
        for conduit in model.conduits:
            # Look for conduits connected to F_INIT ports on this timeline
            if not conduit.sending_component() or not conduit.receiving_component():
                continue  # Ignore model ports
            receiver = model.components[conduit.receiving_component()]
            if receiver not in self.components:
                continue
            if receiver.ports[conduit.receiving_port()].operator is not Operator.F_INIT:
                continue
            sender = model.components[conduit.sending_component()]

            # Add dependency
            if sender in self.components:
                dependencies.append((sender, receiver))
            else:
                assert sender.timeline is not None
                try:
                    subtl = sender.timeline.relative_to(self.timeline)
                except ValueError:
                    # This exception means that sending_component.timeline is not a
                    # subtimeline of us, therefore this conduit is not relevant for
                    # determining the order in this timeline.
                    continue
                # Add a dependency for each ancestor component in our timeline:
                for sending_ancestor in self[Timeline([subtl[0]])].parent_components:
                    dependencies.append((sending_ancestor, receiver))
        return dependencies

    def _topological_sort(
        self, dependencies: list[tuple[int, int]], num_groups: int
    ) -> list[int]:
        """Sort groups topologically, use group number as tie breaker.

        Algorithm based on networkx.algorithms.dag.lexicographical_topological_sort, see
        https://networkx.org/documentation/stable/_modules/networkx/algorithms/dag.html#lexicographical_topological_sort
        """
        indegrees = [0] * num_groups
        for _, to in dependencies:
            indegrees[to] += 1
        zero_indegree = [i for i in range(num_groups) if indegrees[i] == 0]
        heapq.heapify(zero_indegree)

        order = []
        while zero_indegree:
            node = heapq.heappop(zero_indegree)
            order.append(node)
            for from_, to in dependencies:
                if from_ != node:
                    continue
                indegrees[to] -= 1
                if indegrees[to] == 0:
                    heapq.heappush(zero_indegree, to)

        if any(indegrees):
            raise ValueError("Graph contains a cycle")
        return order
