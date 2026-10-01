class ToolError(Exception):
    """Raised for expected tool failures (bad input, missing key, API said no).

    The agent loop sends the message back to the model as an error result,
    so write messages the model can act on.
    """
