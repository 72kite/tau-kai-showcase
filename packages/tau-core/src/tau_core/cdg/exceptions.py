class CoreDirectiveViolation(PermissionError):
    """Raised when the CDG denies a tool call outright. Not catchable-and-retryable by the LLM loop."""

    def __init__(self, tool_call: str, reason: str, rule_id: str | None = None):
        self.tool_call = tool_call
        self.reason = reason
        self.rule_id = rule_id
        super().__init__(f"CDG denied '{tool_call}': {reason}" + (f" (rule={rule_id})" if rule_id else ""))


class ApprovalRequiredError(PermissionError):
    """Raised when a tool call matches a require-approval rule and no valid, matching approval token was supplied."""

    def __init__(self, tool_call: str, reason: str, rule_id: str | None = None, request_id: str | None = None):
        self.tool_call = tool_call
        self.reason = reason
        self.rule_id = rule_id
        self.request_id = request_id
        super().__init__(f"CDG requires human approval for '{tool_call}': {reason}")
