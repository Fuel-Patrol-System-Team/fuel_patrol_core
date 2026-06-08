
import pytest

from app.tasks import calculate_auto_rpm


@pytest.mark.django_db
def test_rpm_auto_task():
    task = calculate_auto_rpm.si("90ca213c-ed29-444c-89e9-709d504f3245", None, False )
    result = task.apply_async()
    assert result.successful()