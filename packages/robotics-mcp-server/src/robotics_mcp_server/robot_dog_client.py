"""Robot-dog client placeholder.

project-tau-plan.md Phase 5 explicitly defers this: "add the robot dog once its SDK/ROS
integration is stable." This class exists so the server's client-selection interface
(ROBOT_TYPE=drone|robot_dog) is already in place, but every method fails loudly rather than
silently no-op'ing or faking a response - a misconfigured ROBOT_TYPE=robot_dog must not look
like working hardware control.
"""


class RobotDogIntegrationPending(NotImplementedError):
    pass


class RobotDogClient:
    def _not_built(self, action: str):
        raise RobotDogIntegrationPending(
            f"Robot dog {action} is not implemented yet - SDK/ROS integration is deferred "
            "(see project-tau-plan.md Phase 5). Set ROBOT_TYPE=drone until this is built."
        )

    def get_telemetry(self) -> dict:
        self._not_built("telemetry")

    def start_patrol(self, waypoints: list) -> dict:
        self._not_built("patrol")

    def return_to_home(self) -> dict:
        self._not_built("return-to-home")

    def emergency_stop(self) -> dict:
        self._not_built("emergency stop")
