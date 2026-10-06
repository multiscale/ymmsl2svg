import heapq
import itertools

from ymmsl.v0_2 import (
    Component,
    Model,
    Operator,
    Reference,
    Timeline,
    check_timelines,
)
from ymmsl.v0_2.timeline_resolver import TimelineChecker


def create_timeline_nodes(model: Model) -> "TimelineNode":
    """Create all timeline nodes for a model and return the root node."""
    checker = check_timelines(model)
    root = TimelineNode(Timeline([]), None)

    for component in model.components.values():
        component.timeline = checker.component_timeline(component.name)
        node = root[component.timeline.parent]
        node.add_component(component, checker)

    root.calculate_component_order(model)
    return root


class TimelineNode:
    def __init__(self, timeline: Timeline, parent: "TimelineNode | None") -> None:
        self.timeline = timeline
        """Timeline for this node, relative to the model."""
        self.parent = parent
        """Parent of this node, will be None for the root timeline."""

        self.children: dict[Reference, TimelineNode] = {}
        """Child timeline nodes for direct subtimelines."""
        self.components: list[Component] = []
        """Components that are part of this timeline."""
        self.parent_components: list[Component] = []
        """Parent components, i.e. those with O_I or S ports that send/receive in
        this timeline."""
        self.port_timelines: dict[Reference, Timeline] = {}
        """Timeline (relative to the model) of each O_I and S port of our components,
        by full port reference, as determined by ymmsl's TimelineChecker."""

    def __getitem__(self, timeline: Timeline) -> "TimelineNode":
        """Get a sub-timeline of this one, creating a new one if required."""
        node = self
        for part in timeline:
            if part not in node.children:
                node.children[part] = TimelineNode(node.timeline + part, node)
            node = node.children[part]
        return node

    def add_component(self, component: Component, checker: TimelineChecker) -> None:
        """Register a component with this timeline node"""
        self.components.append(component)
        for port in component.ports.values():
            if port.operator in (Operator.O_I, Operator.S):
                timeline = checker.timeline_for_port(component.name + port.name)
                self.port_timelines[component.name + port.name] = timeline
                node = self[timeline.relative_to(self.timeline)]
                if component not in node.parent_components:
                    node.parent_components.append(component)

    def calculate_component_order(self, model: Model) -> None:
        """Determine order of components in this timeline."""
        # Group all components with shared timelines
        component_groups = self._group_components(model)
        group_per_component = {}
        for i, group in enumerate(component_groups):
            for component in group:
                group_per_component[component] = i

        # Find dependencies between the groups
        group_deps: set[tuple[int, int]] = set()
        for sender, receiver in self._finit_dependencies(model):
            # Check that we don't have dependencies between components in the same group
            if group_per_component[sender] == group_per_component[receiver]:
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

    def _group_components(self, model: Model) -> list[list[Component]]:
        """Group all components that (indirectly) share a timeline.

        For example, a timeline bridge connecting two components A and B will have two
        subtimelines: ":A" shared between components A and bridge, and ":B" shared
        between components B and bridge. All three components are part of a single
        group. Components whose subtimelines are declared as matching timelines (e.g.
        an interact coupling) are linked in the same way.
        """
        links_per_component: dict[Component, list[list[Component]]] = {}
        for name, link in self._component_links(model):
            if len(link) > 2:
                raise RuntimeError(
                    f"Unsupported coupling graph: subtimeline '{name}' has "
                    f"{len(link)} parent components, but we support no more than 2."
                )
            for component in link:
                links_per_component.setdefault(component, []).append(link)

        for comp, links in links_per_component.items():
            if len(links) > 2:
                raise RuntimeError(
                    f"Unsupported coupling graph: component '{comp.name}' shares "
                    f"{len(links)} subtimelines, but we support no more than 2."
                )

        component_groups = []
        done = set()
        for component in self.components:
            if component in done:
                continue
            if len(links_per_component.get(component, [])) > 1:
                # We'll get back to this component later, the first component in the
                # group should be one with only a single shared timeline
                continue

            group = []
            next_component = component
            while next_component is not None:
                group.append(next_component)
                component = next_component
                next_component = None
                for link in links_per_component.get(component, []):
                    for comp in link:
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

    def _component_links(self, model: Model) -> list[tuple[Timeline, list[Component]]]:
        """Get the sets of components in this timeline that share a subtimeline.

        A subtimeline is shared when it has more than one parent component, or when
        it matches subtimelines of other components (see Model.matching_timelines).
        Matching timelines that are not direct subtimelines of this timeline (e.g.
        those of components in different subtimelines) don't link components here.

        Returns:
            (timeline, components) for each set of components with at least 2 members.
        """
        links = [
            (subtl.timeline, subtl.parent_components)
            for subtl in self.children.values()
            if len(subtl.parent_components) > 1
        ]
        for mt in model.matching_timelines or []:
            link: list[Component] = []
            for timeline in mt.matches:
                if len(timeline) == 0 or timeline.parent != self.timeline:
                    continue
                subtl = self.children.get(timeline[-1])
                if subtl is None:
                    continue
                for component in subtl.parent_components:
                    if component not in link:
                        link.append(component)
            if len(link) > 1:
                links.append((mt.head, link))
        return links

    def _ancestors_of(self, component: Component) -> list[Component]:
        """Get ancestors of the component in our timeline."""
        assert component.timeline is not None
        if component.timeline.parent == self.timeline:
            return [component]
        try:
            subtl = component.timeline.parent.relative_to(self.timeline)
            return self.children[subtl[0]].parent_components
        except ValueError:  # component is not in a subtimeline
            return []

    def _finit_dependencies(self, model: Model) -> set[tuple[Component, Component]]:
        """Determine dependencies resulting from conduits connected to F_INIT ports.

        Returns a list of component pairs, where the second component depends on a
        message of the first component.
        """
        dependencies: set[tuple[Component, Component]] = set()
        # Determine dependencies between component groups
        for conduit in model.conduits:
            # Look for conduits connected to F_INIT ports relevant for this timeline
            if not conduit.sending_component() or not conduit.receiving_component():
                continue  # Ignore model ports
            receiver = model.components[conduit.receiving_component()]
            if receiver.ports[conduit.receiving_port()].operator is not Operator.F_INIT:
                continue
            sender = model.components[conduit.sending_component()]

            # Both sender and receiver must be in our timeline, or a subtimeline
            sender_ancestors = self._ancestors_of(sender)
            receiver_ancestors = self._ancestors_of(receiver)
            if sender_ancestors == receiver_ancestors:
                # Sender and receiver have the same ancestor components in our timeline,
                # so we can ignore this conduit for sequencing this timeline
                continue
            for dependency in itertools.product(sender_ancestors, receiver_ancestors):
                dependencies.add(dependency)
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
