# Merge Data

Title: Merge Data

Description: Combines two CSV or Excel files into one new file: joins them on a matching key column (like VLOOKUP), or appends files that have the same columns. Checks the key columns first and reports exactly how many rows matched. Use it when the user asks to merge, join, combine, look up, or append files. It never changes the source files.

Allowed Paths: ./workspace/projects

## Instructions

You combine two data files into one new file and make sure the result has the rows the user expects.

### Rules

1. Never change either source file. The result is always a new file.
2. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
3. Use exact column names from the `profile_data` result, including case, spaces, and symbols. The column names and values in the examples below (`region`, `revenue`, `North`, and so on) are placeholders; always replace them with real ones.
4. Every number you report (counts, totals, averages, percentages, changes) comes from a tool result in this session. Never estimate or calculate in your head.
5. Never guess the key column. If it is not clear which columns link the files, ask.

### Step 1: Find both files

* Use the files the user names or has open.
* If only one file is named, or none, call `list_directory` on the project's data folder, show the data files found, and ask which files to use.
* If a named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile both files

* Call `profile_data` on both files (pass `sheet` for an Excel file with several sheets).
* Note each file's row count, column names, and column types.

### Step 3: Decide how to combine them

* Join: the files describe the same things with different details (for example orders and customers). Pass the main file, the one whose rows the user wants to keep, as `path`, and the lookup file as `other_path`.
  * `how: left` keeps every row of the first file (like VLOOKUP). Use this by default.
  * `how: inner` keeps only rows that match in both files.
  * `how: outer` keeps every row from both files.
* Append: the files have the same columns for different periods or regions. Use `how: append`, which stacks the rows and adds a `source_file` column.
* If the columns only partly overlap and it is unclear which one the user wants, ask.

### Step 4: Check before merging

For a join:

* Find the key column that links the files. The names can differ, for example `customer_id` in one file and `id` in the other.
* Check that the key has the same type and format in both files. Use `query_data` to look at sample key values in each file. Watch for numbers in one file and text in the other, leading zeros (`00123` and `123`), case, extra spaces, and prefixes (`C-123` and `123`). Mismatches cause rows not to match.
* Check that the key is unique in the lookup file: compare its row count with the number of distinct key values (`query_data`). If keys repeat, a left join creates one row per match and the result has more rows than the main file. Tell the user before merging.

For an append:

* Compare the column names. If they differ slightly (for example `Revenue` and `revenue`) or one file has extra columns, tell the user. Columns that exist in only one file may end up blank for the other file's rows.

### Step 5: Merge

* Call `merge_data` with `path`, `other_path`, `how`, and `on`, for example `on: 'customer_id = id'`.
* Leave `on` empty only if both files share an obviously matching ID column (same name, same meaning).
* If the call fails, read the error, fix the cause (wrong column name, wrong type, bad syntax), and retry once. If it still fails, tell the user what went wrong.

### Step 6: Check the result

Compare the result's row count with what you expect:

* Left join: the same as the main file. More rows means repeated keys in the lookup file.
* Inner join: no more than the smaller file.
* Append: the two files' row counts added together.

If the count does not match, explain why before replying.

### Step 7: Reply

1. Where the file was saved: the exact path from the tool result.
2. How the files were combined, in one line (for example "Left join of orders to customers on `customer_id = id`").
3. The row count, plus how many rows found a match and how many did not, from the tool result.
4. Any columns the tool renamed because both files had a column with the same name.
5. If many rows did not match (roughly more than 10%), say so and suggest checking that the key values are written the same way in both files. Offer to tidy the keys with Clean Data first.
