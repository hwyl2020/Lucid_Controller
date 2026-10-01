import pytest

from app.cameras.camera_device import InvalidValueError, NumericRange, Roi, RoiLimits

LIMITS = RoiLimits(
    sensor_width=640,
    sensor_height=480,
    min_width=64,
    min_height=64,
    width_increment=8,
    height_increment=2,
    offset_x_increment=8,
    offset_y_increment=2,
)


class TestNumericRange:
    def test_accepts_in_range(self):
        NumericRange(0, 10).validate(5)

    @pytest.mark.parametrize("value", [-0.1, 10.1])
    def test_rejects_out_of_range(self, value):
        with pytest.raises(InvalidValueError):
            NumericRange(0, 10).validate(value)

    def test_increment_is_relative_to_minimum(self):
        r = NumericRange(1, 100, increment=3)
        r.validate(4)
        with pytest.raises(InvalidValueError):
            r.validate(5)

    def test_float_increment_tolerates_rounding(self):
        NumericRange(0.0, 1.0, increment=0.1).validate(0.3)


class TestRoiLimits:
    def test_full_frame_is_valid(self):
        LIMITS.validate(LIMITS.full_frame())

    @pytest.mark.parametrize(
        "roi",
        [
            Roi(0, 0, 32, 64),  # width below minimum
            Roi(0, 0, 644, 64),  # width above sensor
            Roi(0, 0, 100, 64),  # width not on increment
            Roi(0, 0, 64, 65),  # height not on increment
            Roi(4, 0, 64, 64),  # offset x not on increment
            Roi(600, 0, 64, 64),  # x + width beyond sensor
            Roi(0, 420, 64, 64),  # y + height beyond sensor
            Roi(-8, 0, 64, 64),  # negative offset
        ],
    )
    def test_rejects_invalid(self, roi):
        with pytest.raises(InvalidValueError):
            LIMITS.validate(roi)

    def test_accepts_aligned_sub_roi(self):
        LIMITS.validate(Roi(576, 416, 64, 64))
