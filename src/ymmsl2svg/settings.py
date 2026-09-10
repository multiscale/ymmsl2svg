from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = True
    """Enable debug visualizations."""

    model_border: float = 2
    """Border width of the model."""

    component_width: float = 100
    """Minimum width of each component."""
    component_height: float = 50
    """Minimum height of each component."""
    component_border: float = 2
    """Border width of a component."""

    text_margin: float = 5
    """Approximate margin around the component name."""

    port_size: float = 7  # TODO: rescale port graphics based on this value?
    """Size of a port. N.B. adjusting this setting only affects spacing, not the actual
    size of the ports."""
    port_margin: float = 10
    """Size between the centers of two ports."""
    resequence_ports: bool = True
    """Allow resequencing ports to reduce conduit crossings. When set to False, the
    ports will be ordered as they are defined in the yMMSL file."""

    conduit_margin: float = 4  # Must be <= port_margin!
    """Spacing between vertical conduit lanes (see ConduitDuct.calc_layout)."""
    hlane_margin: float = 4  # Must be <= port_margin, and > conduit_width!
    """Spacing between horizontal conduit lanes/cruise heights (see
    TopConduitDuct.calc_layout/safe_cruise_height). Two unrelated conduits can
    legitimately end up on adjacent lanes (e.g. two different senders each
    broadcasting past the same middle component to a shared destination further
    along, as in dispatch3.ymmsl's "first" -> "third" conduits) and run parallel
    for a while, so this has to stay enough bigger than conduit_width for the
    gap between their strokes to actually read as two separate lines instead of
    one thick one -- conduit_width + 2 here, not just conduit_width itself."""
    vport_margin: float = 5  # Must be <= port_margin!
    """Spacing between a TopConduitDuct's own virtual (conduit-hop) ports on its
    left/right edge (see VirtualPortPoint) -- separate from port_margin (real
    component ports) so this horizontal-lane spacing can be tightened on its own."""
    conduit_width: float = 2


# Singleton object for global settings
settings = Settings()
