from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = True
    """Enable debug visualizations."""

    check_timelines: bool = True
    """Verify timeline consistency before drawing. Set False for a best-effort
    drawing of models whose timelines don't validate (e.g. time-scale bridges)."""

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

    draw_port_icons: bool = True
    """Render the port icons (diamonds for F_INIT/O_F, circles for O_I/S). Disable via
    `disable_port_icons`, which also collapses the spacing the icons reserved."""

    conduit_margin: float = 4  # Must be <= port_margin!
    conduit_width: float = 2

    def disable_port_icons(self) -> None:
        """Stop drawing port icons and collapse the spacing they reserved.

        The icons are the only thing occupying ``port_size`` (the band between a
        component edge and its conduit duct) and the icon-footprint part of
        ``port_margin``. The diamonds are 6px wide/tall and the circles 5px, so
        ``port_margin`` (10) decomposes as 6px icon footprint + ``conduit_margin`` (4)
        clearance. With no icon the band collapses to 0 and the port spacing drops to
        the bare conduit clearance (``conduit_margin``)."""
        self.draw_port_icons = False
        self.port_size = 0
        self.port_margin = self.conduit_margin


# Singleton object for global settings
settings = Settings()
