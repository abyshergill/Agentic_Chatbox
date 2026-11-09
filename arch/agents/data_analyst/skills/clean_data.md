# Clean Data

Title: Clean Data

Description: Cleans a CSV or Excel dataset and saves a cleaned copy: trims extra spaces, merges inconsistent spellings, fixes letter case, converts numbers stored as text, removes duplicates, and fills or drops missing values. Use it when the user asks to clean, tidy, fix, standardize, or dedupe their data. The original file is never changed.

Allowed Paths: ./workspace/projects

## Instructions

You clean a dataset and save the result as a new file. You fix only the problems the data actually has, and you report every change with its exact count.

### Rules

1. Never change, overwrite, or rename the original file. Always write the result to a new file.
2. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
3. Fix only problems shown by the quality report or a `query_data` check. Do not clean "just in case".
4. Never drop rows or invent values without a stated reason. Every fill and every drop must be justified in the reply.
5. Use exact column names from the `profile_data` result. The column names and values in the examples below (`region`, `north`, `price`, and so on) are placeholders; always replace them with real ones.
6. Every count you report (rows removed, values replaced, cells filled) comes from a tool result. Never estimate.

### Step 1: Find the file

* If the user names a file or has one open, use it.
* If no file is named, call `list_directory` on the project's data folder, show the data files found, and ask which one to use. Do not pick one yourself.
* If the named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile it and find the problems

* Call `profile_data` on the file. For an Excel file with several sheets, pass `sheet` for the sheet to clean; if it is unclear which sheet, ask.
* Call `analyze_data` with `analysis: quality` to find the problems.
* For text columns flagged with inconsistent values, call `query_data` to count rows per distinct value (group by the column). This shows the exact spelling variants and which spelling is most common.
* Note the starting row count; you will report it in the reply.

### Step 3: Plan the steps

Build the step list only from the problems found, in this order. The order matters: tidying text first lets spelling variants and duplicates match, and fixing types first lets fills work on real numbers.

1. Tidy spaces in text columns: `trim`
2. Merge spelling variants, one value per step: `replace | column: old -> new`, for example `replace | region: north -> North`
3. Standardize mixed upper and lower case: `case | column = title`
4. Convert numbers stored as text: `type | column = number`
5. Remove duplicates: `dedupe` for exact duplicate rows, or `dedupe | id` for rows that repeat an ID column
6. Handle missing values, one column per step:
   * A few missing numbers: `fill | column = median`
   * Missing text: `fill | column = Unknown`
   * A row that is useless without that value: `dropna | column`

How to choose:

* Replace: merge only values that are clearly the same thing written differently (case, spaces, typos, abbreviations such as `N.` and `North`). Map each variant to the most common spelling. Do not merge values that could mean different things.
* Type: before converting, check whether any values will not convert (for example `N/A`, `1,200`, `$45`, or `12 kg`). Mention them in the reply, because they may become missing after conversion.
* Dedupe by ID: use it only if the user confirms that the column is a true unique ID. If rows share an ID but differ in other columns, report that instead of choosing a row silently.
* Fill with median: use it when only a small share of a numeric column is missing. If a column is mostly empty, do not fill it; report it and suggest what to do instead.
* Dropna: use it only for columns the rows cannot be used without, such as the ID, the main date, or the main metric.

Check with the user first (list the planned steps and the reason for each) when a step could change the results a lot:

* `dropna` or `dedupe | id` would remove more than about 5% of rows.
* A fill would affect more than about 20% of a column.
* It is unclear whether two spellings mean the same thing.

Run safe steps (`trim`, clear spelling and case fixes, `type`, exact `dedupe`) without asking.

### Step 4: Run the cleaning

* Call `transform_data` with the source path, the steps in the order above, and an output path.
* Output path: save it in the same folder as the source, named `<original name>_cleaned` with the same extension (for example `orders.csv` becomes `orders_cleaned.csv`). Keep the source format unless the user asks for another. For Excel output, the output name must end in `.xlsx`.
* If a file with that name already exists, do not overwrite it; add a number instead (`orders_cleaned_2.csv`).
* If the call fails, read the error, fix the cause (wrong column name, bad step syntax, step in the wrong order), and retry once. If it still fails, tell the user which step failed and why.

### Step 5: Verify

* Call `profile_data` on the cleaned file, then call `analyze_data` with `analysis: quality` on it.
* Confirm that the planned problems are gone and that no new ones appeared (for example new missing values created by `type` conversion).
* Compare the row count before and after. It must match the rows removed by the steps.

### Step 6: Reply

1. Where the cleaned file was saved: the exact path from the tool result.
2. Rows before and after: for example "5,000 rows to 4,912 rows".
3. Each change with its exact count from the tool result, one line each. For example: "Trimmed spaces in 3 text columns (418 cells)" or "Merged `north` and `NORTH` into `North` (57 rows)".
4. What you did not change and why. For example: outliers that may be real values, columns that were mostly empty, values that would not convert, or duplicate IDs with conflicting data.
5. Anything that needs the user's decision, as a short list.

Keep the reply short and scannable. Do not paste raw tool output unless the user asks.
