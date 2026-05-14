"""Job / pipeline control exceptions."""


class CooperativePauseError(Exception):
    """Operator requested pause after the current image step finished."""
