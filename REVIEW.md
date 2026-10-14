# REVIEW.md

## Defects Found in the Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Attendance punch-in | Checking for an existing open record and then inserting can allow duplicate records under concurrent requests without a database-level guarantee. | Send simultaneous punch-in requests for the same employee and date. | A unique constraint or atomic database operation is needed to guarantee a single successful punch-in. Verify the implementation under concurrency. |
| 2 | Attendance response serialization | MongoDB `ObjectId` values cannot be returned directly as ordinary JSON values. | Create an employee or punch-in and inspect the API response for a serialization error. | Convert the returned ID to a string and remove the internal `_id` field where appropriate. |
| 3 | Department summary | Counting employees after expanding their attendance records can count employees multiple times. | Give one employee multiple logs in the same month and compare the department headcount. | Count each employee once before calculating the department-level headcount. |
| 4 | Late leaderboard | Ranking and deterministic tie ordering must be handled separately. | Test employees with equal total late minutes and verify competition ranks and employee-code ordering. | Use MongoDB ranking and sorting stages, then apply the result limit. |
| 5 | Explain endpoint | Simplified queries do not necessarily represent the actual endpoint pipeline, and reading only top-level explain fields can return empty statistics. | Compare the explained pipeline with the real leaderboard pipeline and inspect the returned execution statistics. | Expand the pipeline to include grouping, employee lookup, filtering, ranking, sorting, and limiting. Extract execution statistics from the actual explain response structure. Further verification is required. |
| 6 | Department trend | Date densification may not generate dates when the input contains no records in the requested period. | Request a period with no attendance logs and check whether every calendar date is returned. | Ensure the pipeline handles empty periods and returns the complete date range. Verify the implementation with an empty-period test. |

## Reviewed but Not Automatically Considered Defects

- A request returning HTTP 200 confirms successful execution, but does not prove that business rules or query-plan requirements are satisfied.
- An index scan (`IXSCAN`) is evidence that an index is used for a query; it does not by itself prove that the query is efficient on a large dataset.
- Employees with no attendance logs should still contribute to department headcount if they joined by the end of the reporting period.
- Employees tied on late minutes should receive the same competition rank, while a deterministic secondary sort should control their display order.
- Missing attendance history and a missing `half_day` value should be handled according to the assignment's specified defaults.

## Remaining Verification

The following still require verification before submission:

- Concurrent punch-in, punch-out, and regularization behavior.
- Explain plans for all supported analytics endpoints.
- Correct department headcount with multiple logs and employees with no logs.
- Department trend for ranges containing no attendance records.
- Full API contract compliance, including validation, response fields, and error handling.
- Performance against a large dataset.

