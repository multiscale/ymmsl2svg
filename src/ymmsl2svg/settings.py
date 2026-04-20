from dataclasses import dataclass


@dataclass
class Settings:
    debug: bool = True
    # Component style
    component_border: float = 2


# Singleton object for global settings
settings = Settings()
