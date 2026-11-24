from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = False
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

    lane_margin: float = 8  # Must be >= port_size, and > conduit_width!
    """Spacing between the lanes that conduits run along (see conduit_ducts). O_I and
    S ports are spaced the same way, so they can line up with the lanes below them."""
    conduit_width: float = 2


# Singleton object for global settings
settings = Settings()
