# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB** to manage employee attendance, punch-in/punch-out operations, attendance regularization, and attendance analytics.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo
- Uvicorn

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

## Setup

Clone the repository:

```bash
git clone https://github.com/rajesh7245/hrone-attendance-analytics-api.git
cd hrone-attendance-analytics-api
```

Create and activate a virtual environment on Windows:

```cmd
python -m venv myenv
myenv\Scripts\activate.bat
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Ensure MongoDB is running before starting the application.

## Run the Application

```bash
python -m uvicorn app.main:app --reload --port 8000
```

If port 8000 is unavailable on your machine, use port 8001 instead.

## API Documentation

Once the application starts, open:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Configuration

The application reads its MongoDB connection URI and database name from environment variables. Keep `.env` out of version control.

## Project Structure

```text
hrone-attendance-analytics-api/
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
└── .gitignore
```

## Design and Review Notes

See `DECISIONS.md` for implementation decisions and trade-offs, and `REVIEW.md` for the self-review, limitations, and remaining gaps.

## Submission

This project is submitted as a public GitHub repository for the Employee Attendance & Analytics API assignment.
