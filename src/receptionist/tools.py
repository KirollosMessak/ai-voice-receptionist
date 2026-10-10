"""The receptionist's tools: what the AI is allowed to *do*. Each tool calls the clinic rules,
so a booking the rules reject is reported back to the AI as an error it must explain to the caller."""

import json
from datetime import date, datetime

from langchain_core.tools import tool

from receptionist import clinic

CLINIC_INFO = {
    "hours": "The clinic is open Saturday to Thursday, 10 AM to 6 PM. Closed on Fridays.",
    "location": "Smouha, Alexandria (demo clinic: the address is fictional).",
    "prices": "Consultation fees: dentistry 400 EGP, dermatology 500 EGP, pediatrics 350 EGP.",
    "payment": "We accept cash, cards and mobile wallets.",
    "insurance": "We accept AXA, Allianz and MetLife insurance cards. Bring your card to the visit.",
    "parking": "Free parking is available in front of the clinic.",
    "emergency": "For emergencies, call the ambulance on 123 immediately. The clinic does not handle emergencies.",
}


def make_tools(conn, now_fn=datetime.now):
    """Tools bound to one clinic database. `now_fn` is injectable so tests can fix the date."""

    def _json(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)

    @tool
    def list_doctors(specialty: str = "") -> str:
        """List the clinic's doctors with their specialty, working days, hours and fee.
        Optionally filter by specialty: dentistry, dermatology or pediatrics."""
        docs = clinic.find_doctors(specialty) or clinic.DOCTORS
        return _json(
            [
                {
                    "doctor_id": d.id,
                    "name": d.name,
                    "name_ar": d.name_ar,
                    "specialty": d.specialty,
                    "days": d.days,
                    "hours": f"{d.start:%H:%M}-{d.end:%H:%M}",
                    "fee_egp": d.fee_egp,
                }
                for d in docs
            ]
        )

    @tool
    def find_available_slots(doctor_id: str, day: str) -> str:
        """Free 30-minute appointment times for a doctor on a date (YYYY-MM-DD).
        Only offer the caller times returned by this tool."""
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return "Error: day must be a date in YYYY-MM-DD format."
        doctor = clinic.DOCTOR_BY_ID.get(doctor_id)
        if not doctor:
            return f"Error: unknown doctor_id '{doctor_id}'. Use list_doctors."
        slots = clinic.available_slots(conn, doctor_id, d, now_fn())
        if not slots:
            return _json(
                {
                    "doctor": doctor.name,
                    "date": day,
                    "available": [],
                    "note": f"No free times. {doctor.name} works {', '.join(doctor.days)}.",
                }
            )
        return _json({"doctor": doctor.name, "date": day, "weekday": clinic.WEEKDAYS[d.weekday()], "available": slots})

    @tool
    def book_appointment(
        doctor_id: str, day: str, time: str, patient_name: str, phone: str, caller_confirmed: bool
    ) -> str:
        """Book an appointment. Call this ONLY after you repeated the doctor, date, time, name and phone
        to the caller and they clearly said yes; set caller_confirmed=true only in that case."""
        if not caller_confirmed:
            return "Not booked: repeat the details to the caller and ask them to confirm first."
        try:
            return _json(
                {
                    "booked": True,
                    **clinic.book(conn, doctor_id, date.fromisoformat(day), time, patient_name, phone, now_fn()),
                }
            )
        except (clinic.BookingError, ValueError) as e:
            return f"Not booked: {e}"

    @tool
    def find_my_appointments(phone: str) -> str:
        """Upcoming appointments for a phone number (the caller's own number)."""
        appts = clinic.appointments_for_phone(conn, phone, now_fn())
        return _json(appts) if appts else "No upcoming appointments for this phone number."

    @tool
    def cancel_appointment(appointment_id: int, phone: str) -> str:
        """Cancel an appointment. Requires the phone number it was booked with."""
        try:
            return _json(clinic.cancel(conn, appointment_id, phone))
        except clinic.BookingError as e:
            return f"Not cancelled: {e}"

    @tool
    def clinic_information(topic: str) -> str:
        """Clinic facts. topic: hours, location, prices, payment, insurance, parking or emergency."""
        return CLINIC_INFO.get(topic.strip().lower(), "Available topics: " + ", ".join(CLINIC_INFO))

    return [
        list_doctors,
        find_available_slots,
        book_appointment,
        find_my_appointments,
        cancel_appointment,
        clinic_information,
    ]
