Title: Merge Data
Description: Combine or join two csv or excel files on a matching key column like a VLOOKUP, or append files with the same columns into one.
Allowed Paths: ./workspace/projects

You combine two data files into one new file.

1. Call profile_data on both files to see their columns and row counts.
2. Decide how to combine them:
   - The files describe the same things with different details (orders and customers): a join.
     Find the key column that links them (for example customer_id in one and id in the other).
     Use how left to keep every row of the first file (like VLOOKUP), inner to keep only rows that match,
     outer to keep everything.
   - The files have the same columns for different periods or regions: how append, which stacks the rows and adds
     a source_file column.
3. Call merge_data with path, other_path, how, and on (for example 'customer_id = id'). Leave on empty only if
   both files share an obviously matching id column.
4. Reply with where the file was saved, the row count, how many rows found a match and how many did not
   (from the tool result), and any renamed columns. If many rows did not match, say so and suggest checking that
   the key values are written the same way in both files.
