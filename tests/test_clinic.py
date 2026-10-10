from datetime import date, timedelta

import pytest

from receptionist import clinic
from tests.conftest import NOW, TODAY

SUNDAY = date(2026, 10, 11)  # Dr. Sara (D1, dentistry) works Sun/Tue/Thu 10:00-16:00
FRIDAY = date(2026, 10, 16)


def test_slots_follow_working_days_hours_and_existing_bookings(conn):
    slots = clinic.available_slots(conn, "D1", SUNDAY, NOW)
    assert slots[0] == "10:30" and slots[-1] == "15:30"  # 10:00 is pre-booked; last slot ends at 16:00
    assert "13:00" not in slots  # Omar Hassan's appointment
    assert clinic.available_slots(conn, "D1", FRIDAY, NOW) == []  # doesn't work Fridays


def test_no_slots_in_the_past(conn):
    slots = clinic.available_slots(conn, "D3", TODAY, NOW.replace(hour=12, minute=10))
    assert all(s > "12:10" for s in slots)


def test_booking_a_free_slot_then_it_is_taken(conn):
    result = clinic.book(conn, "D1", SUNDAY, "14:00", "Mona Adel", "01234567891", NOW)
    assert result["time"] == "14:00" and result["fee_egp"] == 400
    assert "14:00" not in clinic.available_slots(conn, "D1", SUNDAY, NOW)
    with pytest.raises(clinic.BookingError, match="not available"):
        clinic.book(conn, "D1", SUNDAY, "14:00", "Someone Else", "01111111111", NOW)


@pytest.mark.parametrize(
    "day, slot, name, phone, error",
    [
        (SUNDAY, "14:00", "Mona", "12345", "valid Egyptian mobile"),
        (SUNDAY, "14:00", "", "01234567891", "name is required"),
        (SUNDAY, "17:00", "Mona", "01234567891", "not available"),  # outside working hours
        (FRIDAY, "11:00", "Mona", "01234567891", "not available"),  # day off
        (TODAY + timedelta(days=30), "11:00", "Mona", "01234567891", "next 14 days"),
    ],
)
def test_invalid_bookings_are_rejected(conn, day, slot, name, phone, error):
    with pytest.raises(clinic.BookingError, match=error):
        clinic.book(conn, "D1", day, slot, name, phone, NOW)


def test_phone_formats_are_normalized():
    assert clinic.normalize_phone("+20 123 456 7891") == "01234567891"
    assert clinic.normalize_phone("0123-456-7891") == "01234567891"


def test_cancel_requires_the_booking_phone_number(conn):
    appt = clinic.appointments_for_phone(conn, "01122334455", NOW)[0]
    with pytest.raises(clinic.BookingError):
        clinic.cancel(conn, appt["appointment_id"], "01099999999")  # someone else's number
    clinic.cancel(conn, appt["appointment_id"], "01122334455")
    assert clinic.appointments_for_phone(conn, "01122334455", NOW) == []
    assert "13:00" in clinic.available_slots(conn, "D1", SUNDAY, NOW)  # slot is free again
