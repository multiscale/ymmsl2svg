from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = True
    # Component style
    component_border: float = 2
    """Border width of a component"""
    port_size: float = 7  # TODO: rescale port sizes based on this value?
    """Size of a port"""
    port_margin: float = 10
    """Size between the centers of two ports"""


# Singleton object for global settings
settings = Settings()
