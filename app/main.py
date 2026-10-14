
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from calendar import monthrange

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import ObjectId
from bson.decimal128 import Decimal128

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=3000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


# ---------- Common helpers ----------

def fail(status_code: int, message: str):
    raise HTTPException(status_code=status_code, detail=message)


def rounded(value) -> float:
    return float(
        Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def rounded_rate(value) -> float:
    return float(
        Decimal(str(value)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        fail(422, "Date must use YYYY-MM-DD format")


def parse_month(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        fail(422, "Month must use YYYY-MM format")
    year, month = map(int, value.split("-"))
    return year, month


def parse_shift(value: str) -> time:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        fail(422, "Shift time must use HH:MM format")
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def epoch_ms_to_utc(value: Optional[int]) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)

    if isinstance(value, bool) or not isinstance(value, int):
        fail(422, "Timestamp must be an integer in epoch milliseconds")

    if value < 100_000_000_000:
        fail(422, "Timestamp must be in milliseconds, not seconds")

    try:
        return datetime.fromtimestamp(value / 1000, UTC).replace(
            microsecond=0
        )
    except (ValueError, OverflowError, OSError):
        fail(422, "Timestamp is outside the supported range")


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def attendance_date(punch_in: datetime, employee: dict) -> str:
    local = as_utc(punch_in).astimezone(IST)
    start = parse_shift(employee["shift_start"])
    end = parse_shift(employee["shift_end"])

    # For overnight shifts, early-morning punches belong to
    # the previous calendar day.
    if end <= start and local.time().replace(tzinfo=None) < end:
        local -= timedelta(days=1)

    return local.date().isoformat()


def shift_start_utc(day: str, shift_start: str) -> datetime:
    local_day = parse_date(day)
    shift_time = parse_shift(shift_start)
    local_start = datetime.combine(local_day, shift_time, tzinfo=IST)
    return local_start.astimezone(UTC)


def shift_end_utc(day: str, employee: dict) -> datetime:
    local_day = parse_date(day)
    start = parse_shift(employee["shift_start"])
    end = parse_shift(employee["shift_end"])

    if end <= start:
        local_day += timedelta(days=1)

    return datetime.combine(local_day, end, tzinfo=IST).astimezone(UTC)


def calculate_late_minutes(
    punch_in: datetime, day: str, employee: dict
) -> int:
    actual = as_utc(punch_in)
    scheduled = shift_start_utc(day, employee["shift_start"])
    elapsed = int((actual - scheduled).total_seconds() // 60)

    # The grace period is strictly greater than 10 minutes.
    return elapsed if elapsed > 10 else 0


def calculate_work_hours(
    punch_in: datetime, punch_out: datetime
) -> float:
    seconds = (as_utc(punch_out) - as_utc(punch_in)).total_seconds()
    return rounded(Decimal(str(seconds)) / Decimal("3600"))


def calculate_overtime(
    punch_out: datetime, day: str, employee: dict
) -> int:
    actual = as_utc(punch_out)
    scheduled_end = shift_end_utc(day, employee)
    minutes = int((actual - scheduled_end).total_seconds() // 60)

    # Overtime is recorded only when at least 30 minutes are completed.
    return minutes if minutes >= 30 else 0


def calculate_half_day(work_hours: float) -> bool:
    return work_hours < 4.50


def public_document(document: dict) -> dict:
    result = dict(document)
    result.pop("_id", None)
    if "id" not in result and "_id" in document:
        result["id"] = str(document["_id"])
    return result


def validate_emp_code(value: str) -> str:
    if not re.fullmatch(r"EMP\d{4,6}", value):
        fail(422, "emp_code must match EMP followed by 4 to 6 digits")
    return value


def ensure_page(page: int, page_size: int):
    if page < 1:
        fail(422, "page must be at least 1")
    if not 1 <= page_size <= 100:
        fail(422, "page_size must be between 1 and 100")


# ---------- Request models ----------

class EmployeeIn(BaseModel):
    emp_code: str
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    department: str = Field(min_length=1)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def check_emp_code(cls, value):
        return validate_emp_code(value)

    @field_validator("shift_start", "shift_end")
    @classmethod
    def check_shift(cls, value):
        parse_shift(value)
        return value

    @field_validator("joined_on")
    @classmethod
    def check_joined_on(cls, value):
        parse_date(value)
        return value


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("emp_code")
    @classmethod
    def check_emp_code(cls, value):
        return validate_emp_code(value)


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("emp_code")
    @classmethod
    def check_emp_code(cls, value):
        return validate_emp_code(value)


class RegularizeRequest(BaseModel):
    status: str
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    by: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None:
            epoch_ms_to_utc(value)
        return value



# ---------- Database indexes ----------

@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_employee",
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="ix_attendance_status_date",
    )
    db.employees.create_index(
        [("department", ASCENDING), ("joined_on", ASCENDING)],
        name="ix_employee_department_joined",
    )


# ---------- Health ----------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )


# ---------- Employees ----------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    document = body.model_dump()
    document["created_at"] = datetime.now(UTC).replace(microsecond=0)

    try:
        result = db.employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    document["id"] = str(result.inserted_id)
    document.pop("_id", None)
    return document


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    filters = {}
    if department:
        filters["department"] = department

    total = db.employees.count_documents(filters)
    items = list(
        db.employees.find(filters, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------- Attendance punch-in ----------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY",
        )

    punched_at = epoch_ms_to_utc(body.punched_at)
    day = attendance_date(punched_at, employee)

    if day < employee["joined_on"]:
        raise HTTPException(
            status_code=422,
            detail="Attendance cannot precede the employee's joining date",
        )

    document = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": calculate_late_minutes(
            punched_at, day, employee
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    document["id"] = str(result.inserted_id)
    document.pop("_id", None)
    return document


# ---------- Attendance listing ----------

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    filters = {}

    if emp_code:
        validate_emp_code(emp_code)
        filters["emp_code"] = emp_code

    if status and status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(status_code=422, detail="Invalid status")

    if status:
        filters["status"] = status

    if date_from:
        parse_date(date_from)
    if date_to:
        parse_date(date_to)

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )

    if date_from or date_to:
        filters["date"] = {}
        if date_from:
            filters["date"]["$gte"] = date_from
        if date_to:
            filters["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(filters)
    cursor = (
        db.attendance_logs.find(filters)
        .sort(
            [("date", DESCENDING), ("emp_code", ASCENDING)]
        )
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = []
    for document in cursor:
        document["id"] = str(document.pop("_id"))
        items.append(document)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------- Attendance punch-out ----------

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    punched_out_at = epoch_ms_to_utc(body.punched_at)

    # A punch-out may close an attendance record from the previous
    # calendar day when the employee works an overnight shift.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched_out_at},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No matching punch-in record found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record is already punched out",
        )

    punched_in_at = as_utc(record["punch_in"])
    if punched_out_at <= punched_in_at:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if punched_out_at > punched_in_at + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    hours = calculate_work_hours(punched_in_at, punched_out_at)
    overtime = calculate_overtime(
        punched_out_at, record["date"], employee
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_out_at,
                "work_hours": hours,
                "overtime_minutes": overtime,
                "half_day": calculate_half_day(hours),
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was changed concurrently",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    return updated


# ---------- Attendance regularization ----------

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    validate_emp_code(emp_code)
    parse_date(date)

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    if body.status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(status_code=422, detail="Invalid status")

    non_present = body.status in {"ABSENT", "LEAVE"}

    if non_present and (
        body.punch_in is not None or body.punch_out is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="ABSENT and LEAVE cannot have punch timestamps",
        )

    if not non_present and body.punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="A presence status requires punch_in",
        )

    new_in = (
        None if non_present else epoch_ms_to_utc(body.punch_in)
    )
    new_out = (
        None if non_present or body.punch_out is None
        else epoch_ms_to_utc(body.punch_out)
    )

    if new_in is not None and attendance_date(new_in, employee) != date:
        raise HTTPException(
            status_code=422,
            detail="punch_in must belong to the attendance date",
        )

    if new_out is not None:
        if new_in is None or new_out <= new_in:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in",
            )
        if new_out > new_in + timedelta(hours=24):
            raise HTTPException(
                status_code=422,
                detail="Punch-out cannot exceed 24 hours after punch-in",
            )

    new_hours = (
        calculate_work_hours(new_in, new_out)
        if new_in is not None and new_out is not None
        else None
    )

    new_values = {
        "status": body.status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": new_hours,
        "late_minutes": (
            calculate_late_minutes(new_in, date, employee)
            if new_in is not None else 0
        ),
        "overtime_minutes": (
            calculate_overtime(new_out, date, employee)
            if new_out is not None else 0
        ),
        "half_day": (
            calculate_half_day(new_hours)
            if new_hours is not None else False
        ),
    }

    changes = {}
    for key, new_value in new_values.items():
        old_value = record.get(key)
        if key in {"punch_in", "punch_out"}:
            if old_value is not None:
                old_value = as_utc(old_value)
            if new_value is not None:
                new_value = as_utc(new_value)
        if old_value != new_value:
            changes[key] = new_values[key]

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Regularization contains no changes",
        )

    history_entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.by,
        "reason": body.reason,
        "changes": changes,
    }

    # Compare-and-set prevents silently overwriting a concurrent edit.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": changes,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    return updated


# ---------- Monthly employee analytics ----------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    validate_emp_code(emp_code)
    year, month_number = parse_month(month)

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    first_day = date(year, month_number, 1)
    last_day = date(year, month_number, monthrange(year, month_number)[1])

    records = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {
            "$gte": first_day.isoformat(),
            "$lte": last_day.isoformat(),
        },
    }))

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = sum(
        1 for record in records
        if record["status"] in present_statuses
        and date.fromisoformat(record["date"]).weekday() < 5
    )

    working_days = 0
    current = max(first_day, date.fromisoformat(employee["joined_on"]))
    while current <= last_day:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    late_minutes = sum(record.get("late_minutes", 0) for record in records)
    overtime_minutes = sum(
        record.get("overtime_minutes", 0) for record in records
    )
    completed_hours = sum(
        record.get("work_hours") or 0 for record in records
    )

    return {
        "emp_code": emp_code,
        "name": employee["name"],
        "department": employee["department"],
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "attendance_rate": (
            rounded_rate(present_days / working_days)
            if working_days else None
        ),
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "work_hours": rounded(completed_hours),
        "attendance_records": len(records),
    }


# ---------- Department monthly summary ----------

@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    year, month_number = parse_month(month)
    last_day = date(
        year, month_number, monthrange(year, month_number)[1]
    ).isoformat()

    employee_filter = {"joined_on": {"$lte": last_day}}
    if department:
        employee_filter["department"] = department

    employees = list(db.employees.find(employee_filter, {"_id": 0}))
    departments = {}

    for employee in employees:
        name = employee["department"]
        departments.setdefault(name, {
            "department": name,
            "headcount": 0,
            "present_days": 0,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "work_hours": 0,
        })
        departments[name]["headcount"] += 1

    first_day = f"{year:04d}-{month_number:02d}-01"

    logs = db.attendance_logs.find({
        "date": {"$gte": first_day, "$lte": last_day},
    })

    for record in logs:
        name = db.employees.find_one(
            {"emp_code": record["emp_code"]}, {"department": 1}
        )
        if not name:
            continue

        dept = name["department"]
        if department and dept != department:
            continue
        if dept not in departments:
            continue

        row = departments[dept]
        if record["status"] in {"PRESENT", "WFH", "ON_DUTY"}:
            if date.fromisoformat(record["date"]).weekday() < 5:
                row["present_days"] += 1

        row["late_minutes"] += record.get("late_minutes", 0)
        row["overtime_minutes"] += record.get("overtime_minutes", 0)
        row["work_hours"] += record.get("work_hours") or 0

    results = []
    for row in departments.values():
        row["work_hours"] = rounded(row["work_hours"])
        results.append(row)

    return {"month": month, "items": sorted(
        results, key=lambda item: item["department"]
    )}


# ---------- Late-arrival leaderboard ----------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
):
    year, month_number = parse_month(month)
    first_day = f"{year:04d}-{month_number:02d}-01"
    last_day = date(
        year, month_number, monthrange(year, month_number)[1]
    ).isoformat()

    employee_filter = {}
    if department:
        employee_filter["department"] = department

    employees = {
        employee["emp_code"]: employee
        for employee in db.employees.find(employee_filter)
    }

    pipeline = [
        {"$match": {
            "date": {"$gte": first_day, "$lte": last_day},
            "late_minutes": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
        }},
        {"$sort": {
            "total_late_minutes": -1,
            "_id": 1,
        }},
    ]

    totals = []
    for row in db.attendance_logs.aggregate(pipeline):
        employee = employees.get(row["_id"])
        if employee:
            totals.append({
                "emp_code": row["_id"],
                "name": employee["name"],
                "department": employee["department"],
                "total_late_minutes": row["total_late_minutes"],
            })

    # Competition ranking: equal totals share the same rank.
    results = []
    previous_total = None
    rank = 0

    for index, row in enumerate(totals, start=1):
        if row["total_late_minutes"] != previous_total:
            rank = index
            previous_total = row["total_late_minutes"]

        if rank > limit:
            break

        row["rank"] = rank
        results.append(row)

    return {"month": month, "items": results}


# ---------- Department daily trend ----------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
):
    start = parse_date(from_date)
    end = parse_date(to_date)

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from",
        )
    if (end - start).days > 91:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="Department not found")

    # Aggregate existing daily records in MongoDB.
    pipeline = [
        {"$match": {
            "date": {
                "$gte": start.isoformat(),
                "$lte": end.isoformat(),
            },
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {"$group": {
            "_id": "$date",
            "present_count": {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$eq": ["$half_day", False]},
                    ]},
                    1,
                    {"$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": ["$half_day", True]},
                        ]},
                        0.5,
                        0,
                    ]},
                ]
            }},
            "late_count": {"$sum": {
                "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
            }},
        }},
        {"$project": {
            "_id": 0,
            "date": "$_id",
            "present_count": 1,
            "late_count": 1,
        }},
        {"$sort": {"date": 1}},
    ]

    # Keep the pipeline output keyed by date. Missing calendar dates
    # are added below so the response includes weekends and no-record days.
    daily = {
        row["date"]: row
        for row in db.attendance_logs.aggregate(pipeline)
    }

    output = []
    rates = []
    current = start

    while current <= end:
        day = current.isoformat()
        working_day = current.weekday() < 5
        headcount = db.employees.count_documents({
            "department": department,
            "joined_on": {"$lte": day},
        })
        row = daily.get(day, {})
        present = row.get("present_count", 0)
        late = row.get("late_count", 0)

        rate = (
            rounded_rate(present / headcount)
            if working_day and headcount > 0 else None
        )
        rates.append(rate)

        valid_rates = [
            value for value in rates[-7:] if value is not None
        ]
        moving_average = (
            rounded_rate(sum(valid_rates) / len(valid_rates))
            if valid_rates else None
        )

        output.append({
            "date": day,
            "is_working_day": working_day,
            "headcount": headcount,
            "present_count": present,
            "late_count": late,
            "attendance_rate": rate,
            "moving_avg_7d": moving_average,
        })
        current += timedelta(days=1)

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": output,
    }


# ---------- Query-plan diagnostics ----------

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    department: Optional[str] = None,
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10,
):
    supported = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }
    if endpoint not in supported:
        raise HTTPException(
            status_code=422,
            detail=f"endpoint must be one of {sorted(supported)}",
        )

    if endpoint == "attendance_list":
        match = {}
        if emp_code:
            validate_emp_code(emp_code)
            match["emp_code"] = emp_code
        if date_from or date_to:
            match["date"] = {}
            if date_from:
                parse_date(date_from)
                match["date"]["$gte"] = date_from
            if date_to:
                parse_date(date_to)
                match["date"]["$lte"] = date_to

        query = {
            "find": "attendance_logs",
            "filter": match,
            "sort": {"date": -1, "emp_code": 1},
            "limit": 20,
        }

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="employee_monthly requires emp_code and month",
            )
        validate_emp_code(emp_code)
        year, month_number = parse_month(month)
        query = {
            "find": "attendance_logs",
            "filter": {
                "emp_code": emp_code,
                "date": {
                    "$gte": f"{year:04d}-{month_number:02d}-01",
                    "$lte": date(
                        year, month_number,
                        monthrange(year, month_number)[1],
                    ).isoformat(),
                },
            },
        }

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="department_summary requires month",
            )
        year, month_number = parse_month(month)
        query = {
            "find": "attendance_logs",
            "filter": {
                "date": {
                    "$gte": f"{year:04d}-{month_number:02d}-01",
                    "$lte": date(
                        year, month_number,
                        monthrange(year, month_number)[1],
                    ).isoformat(),
                },
            },
        }

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="late_leaderboard requires month",
            )
        year, month_number = parse_month(month)
        query = {
            "aggregate": "attendance_logs",
            "pipeline": [
                {"$match": {
                    "date": {
                        "$gte": f"{year:04d}-{month_number:02d}-01",
                        "$lte": date(
                            year, month_number,
                            monthrange(year, month_number)[1],
                        ).isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }},
                {"$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }},
            ],
            "cursor": {},
        }

    else:
        if not department or not date_from or not date_to:
            raise HTTPException(
                status_code=422,
                detail="department_trend requires department, date_from, date_to",
            )
        parse_date(date_from)
        parse_date(date_to)
        query = {
            "find": "attendance_logs",
            "filter": {
                "date": {"$gte": date_from, "$lte": date_to}
            },
        }

    try:
        if "find" in query:
            plan = db.command({
                "explain": query,
                "verbosity": "executionStats",
            })
        else:
            plan = db.command({
                "explain": query,
                "verbosity": "executionStats",
            })

        return {
            "endpoint": endpoint,
            "query": query,
            "executionStats": plan.get("executionStats", {}),
            "queryPlanner": plan.get("queryPlanner", {}),
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Explain failed: {str(exc)}",
        )
