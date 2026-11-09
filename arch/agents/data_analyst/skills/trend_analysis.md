# Trend Analysis

Title: Trend Analysis

Description: Analyzes how a metric changes over time: overall growth, period-over-period change, direction, peaks, dips, sudden jumps, and which groups drive the change. Use it when the user asks how something has changed, grown, or trended over time. It describes the past only and never forecasts.

Allowed Paths: ./workspace/projects

## Instructions

You explain clearly how a number has changed over time.

### Rules

1. Read only. Never change the file.
2. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
3. Use exact column names from the `profile_data` result, including case, spaces, and symbols. The column names and values in the examples below (`region`, `revenue`, `North`, and so on) are placeholders; always replace them with real ones.
4. Every number you report (counts, totals, averages, percentages, changes) comes from a tool result in this session. Never estimate or calculate in your head.
5. Do not forecast or predict future values. If the user asks what will happen next, explain that this skill describes past changes only, and describe the recent direction instead.

### Step 1: Find the file

* If the user names a file or has one open, use it.
* If no file is named, call `list_directory` on the project's data folder, show the data files found, and ask which one to use. Do not pick one yourself.
* If the named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile it and find the date and metric

* Call `profile_data` on the file (pass `sheet` for an Excel file with several sheets).
* Find the date column and the metric the user means.
  * If there is no date column, say so and stop.
  * If there are several date columns (for example `order_date` and `ship_date`), use the one that fits the question, or ask.
  * If the date column is stored as text that did not parse, say so and suggest fixing it with Clean Data or Transform Data first.
* Note the first and last dates; you need them to choose the period.

### Step 3: Run the trend

Call `analyze_data` with `analysis: trend`, the `metric`, and a `period` that suits the date range:

* A few weeks of data: `day`
* A few months: `week`
* One or two years: `month`
* Longer: `quarter` or `year`

Aim for roughly 8 to 30 periods: too few hides the pattern, too many makes it noisy.

### Step 4: Find what drives the change (if asked)

* If the user wants to know which group drives the change, first find the largest groups by total metric (`analyze_data` with `analysis: compare`).
* Then call `analyze_data` with `analysis: trend` again for each of the top groups, with a filter such as `filter region == North`. Use at most four groups.
* Say which group's change accounts for most of the overall change.

### Step 5: Interpret the results

* Overall change: from the first to the last period, as a number and a percentage. If the first period's value is 0, give the number only.
* Direction: rising, falling, or flat. Describe the whole series, not only the end points (for example "rose until June, then fell").
* Highest and lowest periods, with their values.
* Sudden jumps or drops: the largest period-over-period changes and when they happened.
* Gaps: periods with no data.
* Incomplete periods: if the tool warns that the last (or first) period is incomplete, repeat the warning. Also give the change between the last two complete periods, because a partial period makes the overall change misleading.
* Seasonality: mention a repeating pattern only if it repeats in at least two years of data.

### Step 6: Reply

1. Answer first: the overall change and direction in one or two sentences.
2. The key numbers: highest and lowest periods, and the biggest jump or drop.
3. The driving groups, if Step 4 was run.
4. Caveats: incomplete periods, gaps, and missing values in the date or metric column.
5. Offer a line or area chart through a dashboard or presentation. Do not build it unless the user asks.
