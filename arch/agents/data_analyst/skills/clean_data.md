Title: Clean Data
Description: Clean a csv or excel dataset: remove duplicates, tidy spaces, fix inconsistent spellings, fill or drop missing values, fix column types, and save a cleaned copy.
Allowed Paths: ./workspace/projects

You clean a dataset and save a cleaned copy. The original file is never changed.

1. Call profile_data on the file (pass sheet for an Excel file with several sheets).
2. Call analyze_data with analysis quality to find the problems.
3. Plan the cleaning steps for the problems you found, in this order:
   - trim (tidy extra spaces in text columns)
   - replace | column: old -> new  (merge spelling variants, e.g. replace | region: north -> North)
   - case | column = title  (when a column mixes upper and lower case)
   - type | column = number  (when numbers were stored as text)
   - dedupe  (exact duplicate rows) or dedupe | id  (duplicate ids)
   - fill | column = median  for a few missing numbers, fill | column = Unknown for missing text,
     or dropna | column  when a row is useless without that value
   Only fix what the quality report shows. Do not drop rows or invent values without a reason.
4. Call transform_data with the path and the steps. Keep the same format as the source unless the user asks;
   for an Excel output use an output ending in .xlsx.
5. Reply with: where the cleaned file was saved, each change with its exact count (from the tool result),
   and anything you chose not to change and why (for example outliers that may be real).
