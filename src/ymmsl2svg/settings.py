from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = True
    """Enable debug visualizations."""

    component_width: float = 100
    """Minimum width of each component."""
    component_height: float = 50
    """Minimum height of each component."""
    component_border: float = 2
    """Border width of a component."""

    port_size: float = 7  # TODO: rescale port graphics based on this value?
    """Size of a port. N.B. adjusting this setting only affects spacing, not the actual
    size of the ports."""
    port_margin: float = 10
    """Size between the centers of two ports."""


# Singleton object for global settings
settings = Settings()
