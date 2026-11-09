# Build Workbook

Title: Build Workbook

Description: Builds an Excel workbook (.xlsx) from a dataset, with a Summary sheet, the full data as a filterable table, pivot tables, group totals, trends, a data quality sheet, native Excel charts, and optional one-tab-per-category splits. Use it when the user asks for an Excel report, workbook, spreadsheet, or xlsx. It never changes the source file.

Allowed Paths: ./workspace/projects

## Instructions

You build a well-organized Excel workbook that answers the user's question and is easy to explore and update.

### Rules

1. Never modify the source data file. The workbook is always a new file.
2. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
3. Use exact column names from the `profile_data` result, including case, spaces, and symbols. The column names and values in the examples below (`region`, `revenue`, `North`, and so on) are placeholders; always replace them with real ones.
4. Every number you report (counts, totals, averages, percentages, changes) comes from a tool result in this session. Never estimate or calculate in your head.

### Step 1: Find the file

* If the user names a file or has one open, use it.
* If no file is named, call `list_directory` on the project's data folder, show the data files found, and ask which one to use. Do not pick one yourself.
* If the named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile it

* Call `profile_data` on the file. For an Excel source with several sheets, pass `sheet`; if it is unclear which sheet to use, ask.
* Note which columns are categorical, numeric, and date or time, and how many distinct values each category has. You need this to choose and check sheets, charts, and `split_by`.

### Step 3: Choose the extra sheets

Every workbook already has a Summary sheet and the full data as a filterable Excel table. Do not add those. Add the extra sheets that answer the user's question, one line per sheet:

* Cross-tab of two categories: `pivot | rows | columns | metric | aggregate`, for example `pivot | region | product | revenue | sum`. Use a column category with few distinct values (about 12 or fewer) so the table fits on screen.
* Totals per group: `group | category | metric`, for example `group | region | revenue`. Gives count, sum, share, mean, and median per group.
* Change over time: `trend | metric | aggregate | period`, for example `trend | revenue | sum | month`. Needs a date column. Choose the period by the date range: day for a few weeks, week for a few months, month for one or two years, quarter or year for longer.
* Data quality check: `quality`. Add it when the user asks, or when the profile showed problems they should see.

If the user made a general request, choose two to four sheets: a `group` sheet for the main category, a `trend` sheet if there is a date column, and a `pivot` if there are two useful categories.

### Step 4: Choose the charts

Add two to four charts, one line per chart:

```
kind | title | x | y | aggregate | series
```

* These kinds become native Excel charts: `bar`, `line`, `area`, `pie`, `donut`, `histogram`, `scatter`, `stacked`, `grouped`.
* `box` and `heatmap` become tables with color scales, not charts. Use them only when that is acceptable to the user.
* Write titles that say what the chart shows, for example "Revenue by region".
* Check every line against the profile: each column exists with that exact spelling, the column types suit the chart, pie and donut charts have 6 slices or fewer, and `series` has about 8 distinct values or fewer.

### Step 5: Split into tabs (only if asked)

* If the user wants one tab per region, customer, or other category, pass `split_by` with that column.
* Check the number of distinct values first. If there are more than about 20, warn the user that the workbook will have that many tabs and ask whether to continue.

### Step 6: Build it

* Call `create_excel` with the data file path, a title, the sheets, the charts, and `split_by` if needed.
* Write the title as the subject of the report, for example "Sales by Region, 2026", not just the file name.
* If the call fails, read the error, fix the cause (wrong column name, wrong type, bad syntax), and retry once. If it still fails, tell the user what went wrong.

### Step 7: Reply

1. Where the workbook was saved: the exact path from the tool result.
2. The sheets in order, with one line each on what the sheet shows.
3. If the user asked a specific question, answer it in one sentence using a number from a tool result.
4. Note that totals and changes are Excel formulas, so they update if numbers in the data are edited.
5. Mention any caveat that affects the numbers, such as missing values in the metric or dates that did not parse.

Keep the reply short. Do not paste sheet specs or raw tool output unless the user asks.
