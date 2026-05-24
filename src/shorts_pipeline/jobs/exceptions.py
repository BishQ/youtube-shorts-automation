"""Job / pipeline control exceptions."""


class CooperativePauseError(Exception):
    """Operator requested pause after the current image step finished."""


class CancelledJobError(Exception):
    """Operator cancelled a job while pipeline work was in progress."""
