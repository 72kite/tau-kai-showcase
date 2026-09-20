import pytest

from robotics_mcp_server.robot_dog_client import RobotDogClient, RobotDogIntegrationPending


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_telemetry(),
        lambda c: c.start_patrol([]),
        lambda c: c.return_to_home(),
        lambda c: c.emergency_stop(),
    ],
)
def test_every_action_fails_loudly_rather_than_faking_control(call):
    """A misconfigured ROBOT_TYPE=robot_dog must never look like working hardware control -
    every method should raise, not silently no-op or return a fake success response."""
    client = RobotDogClient()
    with pytest.raises(RobotDogIntegrationPending):
        call(client)
