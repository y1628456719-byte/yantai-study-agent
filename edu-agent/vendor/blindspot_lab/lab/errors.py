class LabError(Exception):
    """The single business-error mechanism; the host maps this to HTTP."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}
