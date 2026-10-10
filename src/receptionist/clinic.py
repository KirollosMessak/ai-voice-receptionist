"""The clinic's schedule and booking rules. Pure Python: the AI can ask, but these rules decide.

- Doctors work fixed days and hours; appointments are 30-minute slots.
- A slot can only be booked if it exists, is in the future, and is free.
- Appointments are found and cancelled by the patient's phone number only.
"""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "clinic.db"
SLOT_MINUTES = 30
BOOKING_WINDOW_DAYS = 14
PHONE_RE = re.compile(r"^01[0125]\d{8}$")  # Egyptian mobile numbers, e.g. 01012345678
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass(frozen=True)
class Doctor:
    id: str
    name: str
    name_ar: str
    specialty: str
    specialty_ar: str
    days: tuple[str, ...]
    start: time
    end: time
    fee_egp: int


DOCTORS = [
    Doctor(
        "D1",
        "Dr. Sara Nabil",
        "د. سارة نبيل",
        "dentistry",
        "أسنان",
        ("Sunday", "Tuesday", "Thursday"),
        time(10),
        time(16),
        400,
    ),
    Doctor(
        "D2",
        "Dr. Karim Fawzy",
        "د. كريم فوزي",
        "dermatology",
        "جلدية",
        ("Sunday", "Monday", "Wednesday"),
        time(12),
        time(18),
        500,
    ),
    Doctor(
        "D3",
        "Dr. Hana Mostafa",
        "د. هنا مصطفى",
        "pediatrics",
        "أطفال",
        ("Saturday", "Monday", "Tuesday", "Wednesday", "Thursday"),
        time(10),
        time(14),
        350,
    ),
]
DOCTOR_BY_ID = {d.id: d for d in DOCTORS}


class BookingError(ValueError):
    """A request that breaks a clinic rule. The message is safe to read back to the caller."""


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("20") and len(digits) == 12:  # +20 1012345678
        digits = "0" + digits[2:]
    return digits


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DEFAULT_DB, today: date | None = None) -> None:
    """(Re)create the demo database with a few existing appointments, relative to today."""
    today = today or date.today()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # closing(): sqlite3's own context manager commits but does not close the connection
    with closing(connect(db_path)) as conn, conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS appointments;
            CREATE TABLE appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_id TEXT, start TEXT,
                patient_name TEXT, phone TEXT, status TEXT DEFAULT 'booked',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """
        )
        # Pre-booked slots so availability isn't trivially "everything free"
        for doctor in DOCTORS:
            for day in _working_days(doctor, today + timedelta(days=1), 7):
                for hour in (doctor.start.hour, doctor.start.hour + 1):
                    start = datetime.combine(day, time(hour))
                    conn.execute(
                        "INSERT INTO appointments (doctor_id, start, patient_name, phone) VALUES (?, ?, ?, ?)",
                        (doctor.id, start.isoformat(timespec="minutes"), "Existing patient", "01000000000"),
                    )
        # A known appointment for demo cancellation
        d1_day = next(_working_days(DOCTOR_BY_ID["D1"], today + timedelta(days=1), 7))
        conn.execute(
            "INSERT INTO appointments (doctor_id, start, patient_name, phone) VALUES (?, ?, ?, ?)",
            ("D1", datetime.combine(d1_day, time(13)).isoformat(timespec="minutes"), "Omar Hassan", "01122334455"),
        )


def _working_days(doctor: Doctor, start: date, days: int):
    for i in range(days):
        day = start + timedelta(days=i)
        if WEEKDAYS[day.weekday()] in doctor.days:
            yield day


def find_doctors(specialty: str | None = None) -> list[Doctor]:
    if not specialty:
        return DOCTORS
    s = specialty.strip().lower()
    return [d for d in DOCTORS if s in (d.specialty, d.specialty_ar) or s in d.name.lower() or s == d.id.lower()]


def available_slots(conn, doctor_id: str, day: date, now: datetime | None = None) -> list[str]:
    """Free 30-minute slots ("HH:MM") for a doctor on a day. Empty if the doctor doesn't work that day."""
    doctor = DOCTOR_BY_ID.get(doctor_id)
    if not doctor or WEEKDAYS[day.weekday()] not in doctor.days:
        return []
    now = now or datetime.now()
    taken = {
        r["start"]
        for r in conn.execute(
            "SELECT start FROM appointments WHERE doctor_id = ? AND status = 'booked' AND start LIKE ?",
            (doctor_id, f"{day.isoformat()}%"),
        )
    }
    slots, t = [], datetime.combine(day, doctor.start)
    while t + timedelta(minutes=SLOT_MINUTES) <= datetime.combine(day, doctor.end):
        if t > now and t.isoformat(timespec="minutes") not in taken:
            slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=SLOT_MINUTES)
    return slots


def book(
    conn, doctor_id: str, day: date, slot: str, patient_name: str, phone: str, now: datetime | None = None
) -> dict:
    now = now or datetime.now()
    doctor = DOCTOR_BY_ID.get(doctor_id)
    if not doctor:
        raise BookingError(f"Unknown doctor '{doctor_id}'.")
    if not patient_name or len(patient_name.strip()) < 2:
        raise BookingError("The patient's name is required.")
    phone = normalize_phone(phone)
    if not PHONE_RE.match(phone):
        raise BookingError("A valid Egyptian mobile number (11 digits, starting with 01) is required.")
    if day > now.date() + timedelta(days=BOOKING_WINDOW_DAYS):
        raise BookingError(f"Bookings are only open for the next {BOOKING_WINDOW_DAYS} days.")
    if slot not in available_slots(conn, doctor_id, day, now):
        raise BookingError(f"{slot} on {day.isoformat()} is not available for {doctor.name}.")
    start = f"{day.isoformat()}T{slot}"
    with conn:
        cur = conn.execute(
            "INSERT INTO appointments (doctor_id, start, patient_name, phone) VALUES (?, ?, ?, ?)",
            (doctor_id, start, patient_name.strip(), phone),
        )
    return {
        "appointment_id": cur.lastrowid,
        "doctor": doctor.name,
        "specialty": doctor.specialty,
        "date": day.isoformat(),
        "weekday": WEEKDAYS[day.weekday()],
        "time": slot,
        "fee_egp": doctor.fee_egp,
    }


def appointments_for_phone(conn, phone: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE phone = ? AND status = 'booked' AND start > ? ORDER BY start",
        (normalize_phone(phone), now.isoformat(timespec="minutes")),
    )
    out = []
    for r in rows:
        start = datetime.fromisoformat(r["start"])
        out.append(
            {
                "appointment_id": r["id"],
                "doctor": DOCTOR_BY_ID[r["doctor_id"]].name,
                "date": start.date().isoformat(),
                "weekday": WEEKDAYS[start.weekday()],
                "time": start.strftime("%H:%M"),
            }
        )
    return out


def cancel(conn, appointment_id: int, phone: str) -> dict:
    """Only cancels if the phone number matches the booking, so callers can't cancel other people's visits."""
    row = conn.execute("SELECT * FROM appointments WHERE id = ? AND status = 'booked'", (appointment_id,)).fetchone()
    if not row or row["phone"] != normalize_phone(phone):
        raise BookingError("No active appointment with that number was found for this phone number.")
    with conn:
        conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id,))
    return {"appointment_id": appointment_id, "status": "cancelled"}


def all_appointments(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM appointments ORDER BY start")]
