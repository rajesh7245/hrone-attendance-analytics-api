# DECISIONS.md

## 1. Indexes

I use indexes to speed up employee lookups and attendance queries. The employee code index supports finding employees by `emp_code`. Attendance indexes support filtering records by employee and date, and filtering attendance by date for monthly analytics. I chose indexes based on the queries used by the API. I would avoid adding unnecessary indexes because they increase storage and write costs.

## 2. Punch-in race

When two identical punch-in requests arrive simultaneously, both may initially check whether an open attendance record exists. A normal check followed by an insert is not sufficient to prevent duplicates. The database needs a unique constraint or an atomic operation to guarantee that only one request succeeds. The losing request should receive HTTP 409 Conflict.

## 3. Ties

The leaderboard uses competition ranking, so tied employees receive the same rank. For example, ranks can be 1, 2, 2, and 4. Employees with equal late minutes are ordered by employee code for deterministic results. The limit is applied after ranking.

## 4. Headcount

The department summary starts with employees who joined by the end of the selected month and looks up their attendance records. Preserving employees without matching logs ensures they are still included in headcount.

## 5. Scaling

For 100 times more data, I would review query execution plans, benchmark indexes, and consider pagination and MongoDB deployment scaling.